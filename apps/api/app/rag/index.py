"""
Retrieval (spec sections 20-21).

Two lexical signals fused, and a slot for a dense one.

**BM25** over word tokens is the workhorse: exact term matching with saturation,
so a chunk repeating "approval" nine times does not beat one that answers the
question. **Character n-gram TF-IDF** is the second signal, and it is there
because BM25 fails silently on the queries users actually type — a typo, a
plural, or "authorisation" against a document that says "authorization" scores
zero on exact tokens and near-perfect on character trigrams. Fusing them by
reciprocal rank means neither has to be calibrated against the other.

A dense embedder can be injected as a third signal. It is not required and not
faked: with no embedding model configured the index says it is running lexical
only rather than reporting a similarity score computed from nothing. Retrieval
that quietly degrades is worse than retrieval that is honestly weaker, because
the failure shows up as a confidently wrong citation.

Scores are reported as ranks and fused scores, never as percentages. A fused RRF
score has no interpretation as a probability, and rendering it as "87% relevant"
would invent a confidence the method cannot support.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from app.rag.chunking import Chunk

log = logging.getLogger(__name__)

K1 = 1.5
B = 0.75
RRF_K = 60          # standard damping; large enough that rank 1 vs 2 is not decisive
MIN_TOKEN_CHARS = 2

_TOKEN = re.compile(r"[a-z0-9£$%]+")

# Conservative suffix stripping. Without it BM25 scores "approves" against a
# document saying "approval" at exactly zero, which is the most common way a
# lexical index misses an obviously relevant passage. Ordered longest-first so
# "-ations" is tried before "-s".
_SUFFIXES = ("ations", "ation", "ising", "izing", "ised", "ized", "ements",
             "ement", "ingly", "ing", "ies", "ied", "ical", "ally", "als",
             "ers", "est", "ed", "ly", "al", "s")
_MIN_STEM = 4


def stem(token: str) -> str:
    """Strip a common suffix when a recognisable stem remains.

    Deliberately crude: an aggressive stemmer conflates unrelated words, and a
    false match in retrieval costs more than a miss because it surfaces as a
    confident citation to the wrong passage. "policy" and "police" must not
    collapse together, and the trailing-e rule below is written so they do not.

    A single pass is not enough -- "approves" strips to "approve" and "approval"
    to "approv", which still fail to match. Dropping a trailing "e" afterwards
    is what makes the two families meet.
    """
    if token.endswith("ies") and len(token) >= 5:
        return token[:-3] + "y"
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= _MIN_STEM:
            token = token[: -len(suffix)]
            break
    if token.endswith("e") and len(token) >= 6:
        token = token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    return [stem(t) for t in _TOKEN.findall(text.lower())
            if len(t) >= MIN_TOKEN_CHARS]


@dataclass(slots=True)
class Hit:
    chunk: Chunk
    score: float
    rank: int
    signals: dict[str, int] = field(default_factory=dict)   # signal -> its rank

    def as_dict(self) -> dict:
        return {**self.chunk.citation(), "text": self.chunk.text,
                "rank": self.rank, "fused_score": round(self.score, 6),
                "matched_by": sorted(self.signals)}


class RetrievalIndex:
    """In-memory hybrid index over a workspace's chunks.

    In-memory is a deliberate scope limit, not an oversight: pgvector is in the
    stack and `add`/`search` are the two methods a persistent backend has to
    implement. Until documents outgrow a process this avoids a second store to
    keep consistent with the first.
    """

    def __init__(self, *, embedder=None) -> None:
        self.chunks: list[Chunk] = []
        self._tokens: list[list[str]] = []
        self._term_freqs: list[Counter[str]] = []
        self._doc_freq: Counter[str] = Counter()
        self._avg_len: float = 0.0
        self._embedder = embedder
        self._embeddings: np.ndarray | None = None
        self._vectorizer = None
        self._char_matrix = None

    @property
    def size(self) -> int:
        return len(self.chunks)

    @property
    def signals(self) -> list[str]:
        available = ["bm25", "char_ngram"]
        if self._embedder is not None:
            available.append("dense")
        return available

    @property
    def degraded(self) -> bool:
        """True when no dense signal is configured. Surfaced to the caller so a
        weaker index is visible rather than assumed equivalent."""
        return self._embedder is None

    def add(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        for chunk in chunks:
            tokens = tokenize(chunk.indexed_text)
            self.chunks.append(chunk)
            self._tokens.append(tokens)
            counts = Counter(tokens)
            self._term_freqs.append(counts)
            self._doc_freq.update(counts.keys())
        lengths = [len(t) for t in self._tokens]
        self._avg_len = float(np.mean(lengths)) if lengths else 0.0
        self._rebuild_char_index()
        if self._embedder is not None:
            self._rebuild_embeddings()
        return len(chunks)

    # --- signals ----------------------------------------------------------
    def _bm25(self, query_tokens: list[str]) -> np.ndarray:
        scores = np.zeros(len(self.chunks))
        if not query_tokens or not self.chunks:
            return scores
        total = len(self.chunks)
        for term in set(query_tokens):
            df = self._doc_freq.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
            for index, counts in enumerate(self._term_freqs):
                tf = counts.get(term, 0)
                if tf == 0:
                    continue
                length = len(self._tokens[index]) or 1
                denominator = tf + K1 * (1 - B + B * length / (self._avg_len or 1))
                scores[index] += idf * (tf * (K1 + 1)) / denominator
        return scores

    def _rebuild_char_index(self) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer

        corpus = [c.indexed_text for c in self.chunks]
        if not corpus:
            self._vectorizer, self._char_matrix = None, None
            return
        self._vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                                           min_df=1, sublinear_tf=True)
        self._char_matrix = self._vectorizer.fit_transform(corpus)

    def _char_scores(self, query: str) -> np.ndarray:
        if self._vectorizer is None or self._char_matrix is None:
            return np.zeros(len(self.chunks))
        from sklearn.metrics.pairwise import cosine_similarity

        vector = self._vectorizer.transform([query])
        return cosine_similarity(vector, self._char_matrix).ravel()

    def _rebuild_embeddings(self) -> None:
        try:
            vectors = self._embedder([c.indexed_text for c in self.chunks])
            matrix = np.asarray(vectors, dtype=float)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            self._embeddings = matrix / np.clip(norms, 1e-12, None)
        except Exception as exc:
            # A failing embedder drops the index to lexical rather than taking
            # search down with it, and `degraded` starts reporting True.
            log.warning("rag.embedding_failed error=%s; lexical only", exc)
            self._embedder = None
            self._embeddings = None

    def _dense_scores(self, query: str) -> np.ndarray | None:
        if self._embedder is None or self._embeddings is None:
            return None
        try:
            vector = np.asarray(self._embedder([query])[0], dtype=float)
        except Exception:
            return None
        vector = vector / max(float(np.linalg.norm(vector)), 1e-12)
        return self._embeddings @ vector

    # --- search -----------------------------------------------------------
    def search(self, query: str, *, top_k: int = 5,
               min_signals: int = 1) -> list[Hit]:
        """Reciprocal rank fusion over every available signal.

        RRF is used rather than a weighted score sum because the signals are on
        incomparable scales -- a BM25 score of 8 and a cosine of 0.4 cannot be
        added without inventing weights that would need retuning per corpus.
        """
        if not self.chunks or not query.strip():
            return []

        ranked: dict[str, np.ndarray] = {
            "bm25": self._bm25(tokenize(query)),
            "char_ngram": self._char_scores(query),
        }
        dense = self._dense_scores(query)
        if dense is not None:
            ranked["dense"] = dense

        fused = np.zeros(len(self.chunks))
        contributions: dict[int, dict[str, int]] = {}
        for name, scores in ranked.items():
            if not np.any(scores > 0):
                continue
            order = np.argsort(-scores)
            for rank, index in enumerate(order, start=1):
                if scores[index] <= 0:
                    break
                fused[index] += 1.0 / (RRF_K + rank)
                contributions.setdefault(int(index), {})[name] = rank

        candidates = [i for i in np.argsort(-fused)
                      if fused[i] > 0 and len(contributions.get(int(i), {})) >= min_signals]
        return [Hit(chunk=self.chunks[int(i)], score=float(fused[i]), rank=position,
                    signals=contributions.get(int(i), {}))
                for position, i in enumerate(candidates[:top_k], start=1)]

    def stats(self) -> dict:
        # Named `document_count`, not `documents`: the routes merge these stats
        # into a response that already carries a `documents` list, and the
        # collision silently replaced the list with an integer.
        return {"chunks": self.size,
                "document_count": len({c.document_id for c in self.chunks}),
                "signals": self.signals,
                "degraded": self.degraded,
                "note": ("Lexical retrieval only: no embedding model is "
                         "configured, so semantically-phrased questions that "
                         "share no words with the source will not match."
                         if self.degraded else
                         "Hybrid lexical and dense retrieval.")}
