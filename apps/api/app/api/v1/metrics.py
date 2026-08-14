from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import Principal, current_principal, requires
from app.core.security import Permission
from app.semantic.registry import default_registry

router = APIRouter()
_registry = default_registry()   # TODO: read through from the `metrics` table per workspace


@router.get("")
async def list_metrics(approved_only: bool = False,
                       principal: Principal = Depends(current_principal)) -> dict:
    return {"metrics": [m.as_dict() for m in _registry.all(approved_only=approved_only)]}


@router.get("/search")
async def search_metrics(q: str, principal: Principal = Depends(current_principal)) -> dict:
    return {"query": q, "matches": [m.as_dict() for m in _registry.search(q)]}


@router.get("/{key}")
async def get_metric(key: str, principal: Principal = Depends(current_principal)) -> dict:
    metric = _registry.get(key)
    if metric is None:
        raise HTTPException(404, f"Metric '{key}' is not defined in this workspace.")
    return metric.as_dict()


@router.post("/{key}/approve")
async def approve_metric(
    key: str, principal: Principal = Depends(requires(Permission.METRIC_APPROVE))
) -> dict:
    if _registry.get(key) is None:
        raise HTTPException(404, f"Metric '{key}' is not defined.")
    return _registry.approve(key, approver=str(principal.user_id)).as_dict()
