from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/metrics", tags=["infra"])  # reuse infra tag


@router.get("/session/{session_id}")
def session_metrics(session_id: str):  # retained for backward API compatibility
    # Legacy endpoint now returns placeholder since custom aggregation removed.
    try:
        return {
            "session_id": session_id,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0.0,
            "detail": "Session aggregation deprecated; rely on Langfuse dashboard",
        }
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(e))
