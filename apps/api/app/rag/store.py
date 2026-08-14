"""
Per-workspace document stores.

One index per workspace, never one shared index with a filter. A filter is a
line of code somebody can forget; separate indexes make a cross-tenant hit
impossible rather than unlikely — the same reasoning as the session-level
tenant scoping in `app/db/session.py`, applied where the data is text.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.rag.chunking import Chunk, chunk_document
from app.rag.index import RetrievalIndex

log = logging.getLogger(__name__)

MAX_DOCUMENT_CHARS = 2_000_000


class DocumentTooLarge(ValueError):
    pass


@dataclass(slots=True)
class DocumentRecord:
    document_id: str
    title: str
    chunk_count: int
    char_count: int
    ingested_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat())
    flagged_chunks: int = 0

    def as_dict(self) -> dict:
        return {"document_id": self.document_id, "title": self.title,
                "chunk_count": self.chunk_count, "char_count": self.char_count,
                "ingested_at": self.ingested_at,
                "flagged_chunks": self.flagged_chunks}


class DocumentStore:
    """Holds one index per workspace plus the document metadata for each."""

    def __init__(self, *, embedder=None) -> None:
        self._indexes: dict[uuid.UUID, RetrievalIndex] = {}
        self._documents: dict[uuid.UUID, dict[str, DocumentRecord]] = {}
        self._embedder = embedder

    def index_for(self, workspace_id: uuid.UUID) -> RetrievalIndex:
        if workspace_id not in self._indexes:
            self._indexes[workspace_id] = RetrievalIndex(embedder=self._embedder)
            self._documents[workspace_id] = {}
        return self._indexes[workspace_id]

    def ingest(self, workspace_id: uuid.UUID, *, title: str, text: str,
               document_id: str | None = None) -> DocumentRecord:
        from app.rag.injection import scan

        if len(text) > MAX_DOCUMENT_CHARS:
            raise DocumentTooLarge(
                f"Document is {len(text):,} characters; the limit is "
                f"{MAX_DOCUMENT_CHARS:,}. Split it or ingest the relevant section.")
        if not text.strip():
            raise ValueError("Document is empty.")

        document_id = document_id or uuid.uuid4().hex[:12]
        chunks: list[Chunk] = chunk_document(text, document_id=document_id)

        # Scanned at ingestion as well as at retrieval. Finding it now means the
        # person uploading sees it, rather than it surfacing months later inside
        # an answer nobody connects to this file.
        flagged = sum(1 for c in chunks if scan(c.text).suspicious)
        if flagged:
            log.warning("rag.suspicious_document id=%s title=%s flagged_chunks=%d",
                        document_id, title, flagged)

        index = self.index_for(workspace_id)
        index.add(chunks)
        record = DocumentRecord(document_id=document_id, title=title,
                                chunk_count=len(chunks), char_count=len(text),
                                flagged_chunks=flagged)
        self._documents[workspace_id][document_id] = record
        return record

    def documents(self, workspace_id: uuid.UUID) -> list[DocumentRecord]:
        return list(self._documents.get(workspace_id, {}).values())

    def stats(self, workspace_id: uuid.UUID) -> dict:
        index = self.index_for(workspace_id)
        return {**index.stats(),
                "document_count": len(self._documents.get(workspace_id, {}))}

    def reset(self, workspace_id: uuid.UUID) -> None:
        self._indexes.pop(workspace_id, None)
        self._documents.pop(workspace_id, None)


_store = DocumentStore()


def get_store() -> DocumentStore:
    return _store
