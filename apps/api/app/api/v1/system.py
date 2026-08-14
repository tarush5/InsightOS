from fastapi import APIRouter

from app.core.config import settings

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "env": settings.ENV, "version": "0.1.0"}


@router.get("/capabilities")
async def capabilities() -> dict:
    """Tells the frontend what is genuinely available, so the UI can render an
    honest state instead of advertising a feature that is not wired up."""
    return {
        "llm_enabled": settings.llm_enabled,
        "degraded_mode": not settings.llm_enabled,
        "degraded_note": (
            "No LLM provider is configured. All figures, drivers, forecasts and "
            "confidence scores are still computed; narratives are templated."
        ) if not settings.llm_enabled else None,
        # This list is load-bearing: the UI renders an honest state from it, so
        # a capability appears here only once it is implemented and tested end
        # to end. Moving a name from one list to the other is a deliberate act.
        "implemented": [
            "semantic_layer", "sql_validation", "root_cause", "anomaly_detection",
            "forecasting", "significance_testing", "confidence_decomposition",
            "critic_verification", "data_profiling", "causal_inference",
            "scenario_simulation", "alerting", "investigation_history",
            "rbac", "multi_tenancy", "audit_log", "evaluation_harness",
            "rate_limiting", "data_source_connectors", "schema_discovery",
            "pii_classification", "sql_execution", "text_to_sql", "automl_training",
            "report_export", "rag_documents", "prompt_injection_defence",
            "notification_delivery",
        ],
        "not_yet_implemented": [
            "knowledge_graph",
            "kafka_streaming", "opentelemetry_tracing",
        ],
    }
