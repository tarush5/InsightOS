"""
Document routes (spec sections 20-21).

Answers here are grounded in text somebody wrote rather than in computed
figures, which changes what the system can promise. A warehouse number is
reproducible; a passage is a quotation, and the honest form of the answer is
the quotation plus where it came from. So every response carries its citations,
and an answer that cannot cite is not returned.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import Principal, current_principal, requires
from app.core.security import Permission
from app.rag.injection import filter_passages, scan, wrap_for_prompt
from app.rag.store import DocumentTooLarge, get_store

router = APIRouter()

MAX_TITLE = 250


class IngestRequest(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    text: str = Field(min_length=1)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)


@router.post("", status_code=201)
async def ingest(
    payload: IngestRequest,
    principal: Principal = Depends(requires(Permission.DATASOURCE_WRITE)),
) -> dict:
    store = get_store()
    try:
        record = store.ingest(principal.workspace_id, title=payload.title,
                              text=payload.text)
    except DocumentTooLarge as exc:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))

    response = {**record.as_dict(), **store.stats(principal.workspace_id)}
    if record.flagged_chunks:
        # Surfaced at upload, not only at retrieval: the person adding the file
        # is the one who can say whether it should contain that text.
        response["warning"] = (
            f"{record.flagged_chunks} chunk(s) in this document contain "
            "instruction-shaped text addressed to an AI system. They are indexed "
            "but will be treated strictly as data, and the verification step "
            "blocks any answer that adopts them. Review the document if you did "
            "not expect this.")
    return response


@router.get("")
async def list_documents(principal: Principal = Depends(current_principal)) -> dict:
    store = get_store()
    return {"documents": [d.as_dict() for d in store.documents(principal.workspace_id)],
            **store.stats(principal.workspace_id)}


@router.post("/search")
async def search(
    payload: SearchRequest,
    principal: Principal = Depends(current_principal),
) -> dict:
    store = get_store()
    index = store.index_for(principal.workspace_id)
    if index.size == 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No documents have been ingested for this workspace.")

    hits = index.search(payload.query, top_k=payload.top_k)
    kept, report = filter_passages([(h.chunk.chunk_id, h.chunk.text) for h in hits])
    kept_ids = {chunk_id for chunk_id, _ in kept}

    return {
        "query": payload.query,
        "results": [h.as_dict() for h in hits if h.chunk.chunk_id in kept_ids],
        "excluded": report,
        "retrieval": index.stats(),
        # Ranks, never percentages: a fused RRF score has no reading as a
        # probability and presenting one would invent a confidence.
        "note": ("Results are ranked, not scored. The fused score orders "
                 "passages and does not measure how likely each is to answer "
                 "the question."),
    }


@router.post("/ask")
async def ask(
    payload: SearchRequest,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Retrieve and assemble a grounded context.

    The context is returned rather than a synthesised answer. Without a language
    model configured there is nothing to synthesise with, and returning the
    passages is the useful honest output; with one configured the same context
    is what would be sent, wrapped in its data boundary.
    """
    from app.core.config import settings

    store = get_store()
    index = store.index_for(principal.workspace_id)
    if index.size == 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No documents have been ingested for this workspace.")

    hits = index.search(payload.query, top_k=payload.top_k)
    if not hits:
        return {"query": payload.query, "answerable": False, "citations": [],
                "reason": ("Nothing in the indexed documents matches this "
                           "question. " + index.stats()["note"])}

    kept, report = filter_passages([(h.chunk.chunk_id, h.chunk.text) for h in hits])
    if not kept:
        return {"query": payload.query, "answerable": False, "citations": [],
                "excluded": report,
                "reason": ("Every matching passage was excluded by the content "
                           "scan. The documents that matched contain "
                           "instructions addressed to an AI system rather than "
                           "material to cite.")}

    by_id = {h.chunk.chunk_id: h for h in hits}
    citations = [by_id[chunk_id].as_dict() for chunk_id, _ in kept]
    return {
        "query": payload.query,
        "answerable": True,
        "context": wrap_for_prompt([text for _, text in kept]),
        "citations": citations,
        "excluded": report,
        "retrieval": index.stats(),
        "synthesis": ("available" if settings.llm_enabled else "unavailable"),
        "note": ("Passages are returned for citation. No answer was synthesised "
                 "because no language model is configured; the context above is "
                 "exactly what would be sent to one."
                 if not settings.llm_enabled else
                 "Context assembled with its data boundary. Any synthesised "
                 "answer is checked by the verification step before release."),
    }


@router.post("/scan")
async def scan_text(
    payload: IngestRequest,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Check a document for injected instructions without indexing it."""
    result = scan(payload.text)
    return {"title": payload.title, **result.as_dict()}
