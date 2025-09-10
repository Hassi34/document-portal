import os
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.api.routers import analyze as analyze_router
from src.api.routers import chat as chat_router
from src.api.routers import compare as compare_router
from src.api.routers import metrics as metrics_router
from src.observability.langfuse_tracing import init_langfuse
from src.schemas.api.ouput import HealthResponse
from src.utils.config_loader import load_config
from src.utils.env_bootstrap import bootstrap_env
from src.utils.logger import GLOBAL_LOGGER as log
from src.utils.semantic_cache import maybe_init_semantic_cache

# Load API configuration
bootstrap_env(required=["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"])
_cfg = load_config()
_api_intro = _cfg.get("api", {}).get("intro", {})
_api_tags = _cfg.get("api", {}).get("openapi_tags", [])
_api_ops = _cfg.get("api", {}).get("operational-config", {})

API_TITLE = _api_intro.get("API_TITLE", "Document Portal API")
API_DESCRIPTION = _api_intro.get(
    "API_DESCRIPTION",
    "Document Portal for analyzing, comparing, and chatting with documents.",
)
API_VERSION = str(_api_ops.get("API_VERSION", "0.1"))
API_VERSION_END_POINT = _api_ops.get("API_VERSION_END_POINT", "/api/v1")
DEBUG_FLAG = bool(_api_ops.get("DEBUG", False))

app = FastAPI(
    title=API_TITLE,
    description=API_DESCRIPTION,
    version=API_VERSION,
    docs_url=f"{API_VERSION_END_POINT}/docs",
    openapi_url=f"{API_VERSION_END_POINT}/openapi.json",
    openapi_tags=_api_tags,
)

from src.observability.langfuse_tracing import flush_langfuse_events


@app.on_event("startup")
def _startup_init_observability():  # pragma: no cover (startup hook)
    try:
        init_langfuse()  # logs connection status itself
    except Exception as e:  # safety net, never block startup
        log.error("Langfuse startup init failed", error=str(e))


@app.on_event("shutdown")
def _shutdown_flush_langfuse():
    try:
        flush_langfuse_events()  # logs flush status
    except Exception as e:
        log.error("Langfuse flush failed on shutdown", error=str(e))


BASE_DIR = Path(__file__).resolve().parent.parent
app.mount(
    "/static", StaticFiles(directory=str(BASE_DIR / "client/static")), name="static"
)
templates = Jinja2Templates(directory=str(BASE_DIR / "client/templates"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize semantic cache if enabled
maybe_init_semantic_cache(_cfg)

#! Embedded S3 backup removed; use dedicated backup container/task.


@app.get("/", response_class=HTMLResponse)
async def serve_ui(request: Request):
    log.info("Serving UI homepage.")
    resp = templates.TemplateResponse(
        "index.html",
        {"request": request, "api_base": API_VERSION_END_POINT},
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


# Both unversioned and versioned health endpoints, tagged for clarity
@app.get(
    "/health",
    tags=["infra"],
    summary="Health (unversioned)",
    response_model=HealthResponse,
)  # stable infra URL
@app.get(
    f"{API_VERSION_END_POINT}/health",
    tags=["infra"],
    summary="Health (v1)",
    response_model=HealthResponse,
)  # versioned alias
def health() -> Dict[str, str]:
    log.info("Health check passed.")
    return {"status": "ok", "service": "document-portal"}


# Register debug endpoint only when DEBUG flag is enabled
if DEBUG_FLAG:
    # Both unversioned and versioned debug endpoints, grouped under 'infra'
    @app.get(
        "/debug/openapi", tags=["infra"], summary="Debug OpenAPI (unversioned)"
    )  # dev-only
    @app.get(
        f"{API_VERSION_END_POINT}/debug/openapi",
        tags=["infra"],
        summary="Debug OpenAPI (v1)",
    )  # dev-only versioned
    def debug_openapi() -> Dict[str, str]:
        try:
            schema = app.openapi()
            return {"status": "ok", "paths_count": str(len(schema.get("paths", {})))}
        except Exception as e:
            import traceback

            tb = traceback.format_exc()
            log.exception("OpenAPI generation failed")
            return {"status": "error", "error": str(e), "trace": tb}


# ---------- ANALYZE ----------
app.include_router(analyze_router.router, prefix=API_VERSION_END_POINT)

# ---------- COMPARE ----------
app.include_router(compare_router.router, prefix=API_VERSION_END_POINT)

# ---------- CHAT ----------
app.include_router(chat_router.router, prefix=API_VERSION_END_POINT)
# ---------- METRICS ----------
app.include_router(metrics_router.router, prefix=API_VERSION_END_POINT)
