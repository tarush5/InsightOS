"""
Document chunking (spec sections 20-21).

Retrieval quality is decided here, not in the ranker. A chunk that splits a
table from its header, or a policy clause from the condition it depends on,
retrieves as a confident fragment that means something different from what the
document said. Most of this module is about not doing that.

Three rules the splitter follows:

* **Split on structure before length.** Headings, blank lines and list
  boundaries are where a document already tells you its own seams. Falling back
  to a character count is what happens when there is no structure left, not the
  first move.
* **Never split mid-sentence** unless a single sentence exceeds the budget on
  its own, in which case say so on the chunk.
* **Carry the heading path down.** A chunk reading "must be approved by two
  directors" is useless without knowing it sits under *Expenditure > Above
  £50,000*. The heading trail is prepended to the chunk's indexed text and kept
  as metadata for the citation.

Every chunk keeps its character offsets so a citation can point at the source
range rather than at a paraphrase.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

DEFAULT_CHUNK_CHARS = 1200
DEFAULT_OVERLAP_CHARS = 150
MIN_CHUNK_CHARS = 80

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'\[])")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


@dataclass(slots=True)
class Chunk:
    document_id: str
    chunk_id: str
    text: str
    ordinal: int
    start_char: int
    end_char: int
    heading_path: list[str] = field(default_factory=list)
    kind: str = "prose"                  # prose | table | code
    oversized: bool = False              # a single unsplittable unit over budget

    @property
    def indexed_text(self) -> str:
        """What the retriever sees: the heading trail plus the body.

        The trail is indexed as well as displayed because a query naming a
        section ("expenditure approval") should match a chunk that never repeats
        the section's own words.
        """
        if not self.heading_path:
            return self.text
        return " > ".join(self.heading_path) + "\n" + self.text

    def citation(self) -> dict:
        return {"document_id": self.document_id, "chunk_id": self.chunk_id,
                "ordinal": self.ordinal,
                "heading_path": self.heading_path,
                "chars": [self.start_char, self.end_char]}

    def as_dict(self) -> dict:
        return {**self.citation(), "text": self.text, "kind": self.kind,
                "oversized": self.oversized}


def _chunk_id(document_id: str, ordinal: int, text: str) -> str:
    """Stable across re-ingestion of identical content, so a citation recorded
    against an earlier version still resolves when nothing has changed."""
    digest = hashlib.sha256(f"{document_id}:{ordinal}:{text}".encode()).hexdigest()
    return digest[:16]


@dataclass(slots=True)
class _Block:
    text: str
    start: int
    end: int
    headings: list[str]
    kind: str


def _blocks(text: str) -> list[_Block]:
    """Split into structural units, tracking the heading path as it goes."""
    blocks: list[_Block] = []
    headings: list[str] = []
    buffer: list[str] = []
    buffer_start = 0
    cursor = 0
    kind = "prose"
    in_code = False

    def flush(end: int) -> None:
        nonlocal buffer, buffer_start, kind
        body = "\n".join(buffer).strip()
        if body:
            blocks.append(_Block(body, buffer_start, end, list(headings), kind))
        buffer = []
        kind = "prose"

    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        line_start = cursor
        cursor += len(line)

        if stripped.strip().startswith("```"):
            if in_code:
                buffer.append(stripped)
                flush(cursor)
            else:
                flush(line_start)
                buffer_start = line_start
                buffer.append(stripped)
                kind = "code"
            in_code = not in_code
            continue
        if in_code:
            buffer.append(stripped)
            continue

        heading = _HEADING.match(stripped)
        if heading:
            flush(line_start)
            level = len(heading.group(1))
            title = heading.group(2).strip()
            # Trim the trail to this level, then push. A depth-2 heading
            # replaces the previous depth-2 rather than nesting under it.
            del headings[level - 1:]
            headings.append(title)
            buffer_start = cursor
            continue

        if _TABLE_ROW.match(stripped):
            if kind != "table":
                flush(line_start)
                buffer_start = line_start
                kind = "table"
            buffer.append(stripped)
            continue

        if not stripped.strip():
            flush(line_start)
            buffer_start = cursor
            continue

        if not buffer:
            buffer_start = line_start
        buffer.append(stripped)

    flush(cursor)
    return blocks


def _split_long(block: _Block, budget: int, overlap: int) -> list[tuple[str, bool]]:
    """Break an oversized block on sentence boundaries where possible."""
    if len(block.text) <= budget:
        return [(block.text, False)]
    if block.kind in {"table", "code"}:
        # Splitting a table or a code block on sentences produces nonsense, so
        # it is cut on line boundaries and marked oversized.
        pieces: list[tuple[str, bool]] = []
        current: list[str] = []
        length = 0
        for line in block.text.splitlines():
            if length + len(line) > budget and current:
                pieces.append(("\n".join(current), True))
                current, length = [], 0
            current.append(line)
            length += len(line) + 1
        if current:
            pieces.append(("\n".join(current), True))
        return pieces

    sentences = _SENTENCE_END.split(block.text)
    pieces = []
    current = ""
    for sentence in sentences:
        if len(sentence) > budget:
            # One sentence longer than the whole budget. Cutting it is the least
            # bad option, but the chunk is flagged so a citation to it is known
            # to be mid-sentence.
            if current:
                pieces.append((current.strip(), False))
                current = ""
            for start in range(0, len(sentence), budget):
                pieces.append((sentence[start:start + budget], True))
            continue
        if len(current) + len(sentence) + 1 > budget and current:
            pieces.append((current.strip(), False))
            tail = current[-overlap:] if overlap else ""
            current = (tail + " " + sentence) if tail else sentence
        else:
            current = (current + " " + sentence) if current else sentence
    if current.strip():
        pieces.append((current.strip(), False))
    return pieces


def chunk_document(text: str, *, document_id: str,
                   chunk_chars: int = DEFAULT_CHUNK_CHARS,
                   overlap_chars: int = DEFAULT_OVERLAP_CHARS) -> list[Chunk]:
    """Split a document into retrievable, citable chunks."""
    if chunk_chars < MIN_CHUNK_CHARS:
        raise ValueError(f"chunk_chars must be at least {MIN_CHUNK_CHARS}.")
    if overlap_chars >= chunk_chars:
        raise ValueError("overlap_chars must be smaller than chunk_chars.")

    chunks: list[Chunk] = []
    ordinal = 0
    pending: list[_Block] = []
    pending_len = 0

    def emit(blocks: list[_Block]) -> None:
        nonlocal ordinal
        if not blocks:
            return
        body = "\n\n".join(b.text for b in blocks)
        first, last = blocks[0], blocks[-1]
        kind = first.kind if len({b.kind for b in blocks}) == 1 else "prose"
        for piece, oversized in _split_long(
                _Block(body, first.start, last.end, first.headings, kind),
                chunk_chars, overlap_chars):
            if not piece.strip():
                continue
            chunks.append(Chunk(
                document_id=document_id,
                chunk_id=_chunk_id(document_id, ordinal, piece),
                text=piece, ordinal=ordinal,
                start_char=first.start, end_char=last.end,
                heading_path=list(first.headings), kind=kind,
                oversized=oversized))
            ordinal += 1

    for block in _blocks(text):
        # A heading change closes the current chunk: merging text from two
        # sections produces a chunk that answers under the wrong heading.
        heading_changed = bool(pending) and pending[-1].headings != block.headings
        if heading_changed or pending_len + len(block.text) > chunk_chars:
            emit(pending)
            pending, pending_len = [], 0
        pending.append(block)
        pending_len += len(block.text) + 2

    emit(pending)
    return chunks
