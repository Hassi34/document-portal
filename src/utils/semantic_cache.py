import os
from urllib.parse import urlparse

from src.utils.logger import GLOBAL_LOGGER as log


def _load_embeddings_for_provider(cfg: dict, provider: str):
    """Return a LangChain Embeddings instance for the given provider using config.

    Supports providers: "openai", "google", "azure-openai" ("azure" alias allowed).
    Reads model name from cfg["ai"]["embedding_model"][provider].model_name and
    validates required env vars via ApiKeyManager.
    """
    # Late import to avoid heavy deps if not used
    from src.utils.model_loader import ApiKeyManager

    if provider == "azure":
        provider = "azure-openai"

    embedding_block = cfg.get("ai", {}).get("embedding_model", {})
    if provider not in embedding_block:
        raise ValueError(
            f"Embedding provider '{provider}' not found in config.ai.embedding_model"
        )

    model_name = embedding_block[provider].get("model_name")
    keys = ApiKeyManager()

    if provider == "openai":
        keys.require(["OPENAI_API_KEY"])
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=model_name, openai_api_key=keys.get("OPENAI_API_KEY")
        )

    if provider == "google":
        keys.require(["GOOGLE_API_KEY"])
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        return GoogleGenerativeAIEmbeddings(
            model=model_name, google_api_key=keys.get("GOOGLE_API_KEY")
        )

    if provider == "azure-openai":
        # Azure embeddings require a dedicated embedding deployment name
        keys.require(
            [
                "AZURE_OPENAI_API_KEY",
                "AZURE_OPENAI_API_INSTANCE_NAME",
                "AZURE_OPENAI_API_VERSION",
                "AZURE_OPENAI_API_EMBEDDING_DEPLOYMENT_NAME",
            ]
        )
        from langchain_openai import AzureOpenAIEmbeddings

        deployment = keys.get("AZURE_OPENAI_API_EMBEDDING_DEPLOYMENT_NAME")
        api_key = keys.get("AZURE_OPENAI_API_KEY")
        instance = keys.get("AZURE_OPENAI_API_INSTANCE_NAME")
        api_version = keys.get("AZURE_OPENAI_API_VERSION")
        azure_endpoint = f"https://{instance}.openai.azure.com/"
        return AzureOpenAIEmbeddings(
            model=model_name,
            azure_endpoint=azure_endpoint,
            azure_deployment=deployment,
            openai_api_version=api_version,
            api_key=api_key,
        )

    raise ValueError(f"Unsupported embedding provider: {provider}")


def init_semantic_cache(
    redis_url: str, embedding_provider: str = "openai", cfg: dict | None = None
) -> None:
    """Initialize LangChain semantic LLM cache with Redis.

    This sets a global cache via langchain.globals.set_llm_cache().
    embedding_provider: one of "openai", "google", "azure-openai".
    If cfg is provided, the embedding model_name is read from it; otherwise defaults may be used.
    """
    try:
        from langchain.globals import set_llm_cache

        # Try dedicated package submodule first; fallback to community cache
        try:
            from langchain_redis.cache import RedisSemanticCache  # type: ignore
        except Exception:
            from langchain_community.cache import (  # type: ignore
                RedisSemanticCache,
            )

        if cfg is None:
            # Minimal fallback: construct a tiny cfg from env provider
            cfg = {"ai": {"embedding_model": {}}}

        emb = _load_embeddings_for_provider(cfg, embedding_provider)

        # Optional extras from config for consistency with notebooks (best-effort)
        cache_cfg = cfg.get("ai", {}).get("semantic_cache", {}) if cfg else {}
        extras = {}
        for k in ("name", "ttl", "distance_threshold"):
            if k in cache_cfg:
                extras[k] = cache_cfg[k]

        cache = RedisSemanticCache(redis_url=redis_url, embeddings=emb, **extras)  # type: ignore
        set_llm_cache(cache)
        # Avoid logging full redis_url (may include credentials). Log sanitized host only.
        try:
            _p = urlparse(redis_url)
            _host = _p.hostname or "?"
            _port = _p.port
            _scheme = _p.scheme
            _safe = f"{_scheme}://{_host}{(':' + str(_port)) if _port else ''}"
        except Exception:
            _safe = "(unparsed)"
        log.info(
            "Semantic cache initialized",
            redis_host=_safe,
            embedding_provider=embedding_provider,
        )
    except Exception as e:
        log.error("Failed to initialize semantic cache", error=str(e))


def maybe_init_semantic_cache(cfg: dict) -> None:
    """Initialize Redis semantic cache if enabled in config."""
    # Load local .env in non-production environments
    try:
        if os.getenv("ENV", "local").lower() != "production":
            try:
                from dotenv import load_dotenv  # type: ignore

                load_dotenv()
                log.info("Loaded .env for semantic cache initialization")
            except Exception:
                # If dotenv missing, continue; env may already be set by other means
                pass
    except Exception:
        pass

    ai_cfg = cfg.get("ai", {})
    cache_cfg = ai_cfg.get("semantic_cache", {})
    enabled = bool(cache_cfg.get("enabled", False))
    if not enabled:
        log.info("Semantic cache disabled via config")
        return

    # Prefer secure secret loading: API_KEYS bundle -> env -> YAML fallback
    redis_url: str | None = None
    _redis_source = ""
    try:
        from src.utils.model_loader import ApiKeyManager

        _akm = ApiKeyManager()
        redis_url = _akm.get("REDIS_URL")
        if redis_url:
            _redis_source = "secret"
    except Exception:
        # Secrets manager/bundle may not be configured; continue
        pass

    if not redis_url:
        env_val = os.getenv("REDIS_URL")
        if env_val:
            redis_url = env_val
            _redis_source = "env"
        else:
            redis_url = cache_cfg.get("redis_url", "redis://localhost:6379")
            _redis_source = "yaml"

    # In production, avoid silently falling back to localhost if no secret/env was provided
    if os.getenv("ENV", "local").lower() == "production" and _redis_source == "yaml":
        # Log a concise warning and skip semantic cache init to avoid misleading failures
        log.warning(
            "Semantic cache not initialized: REDIS_URL missing in secrets/env; skipping in production"
        )
        return

    provider = cache_cfg.get("embedding_provider", "openai")
    # Allow overriding provider via secrets/env (e.g., EMBEDDING_PROVIDER=azure-openai)
    try:
        from src.utils.model_loader import ApiKeyManager as _AKM

        _prov_override = _AKM().get("EMBEDDING_PROVIDER")
    except Exception:
        _prov_override = None
    if not _prov_override:
        _prov_override = os.getenv("EMBEDDING_PROVIDER") or os.getenv(
            "AI_EMBEDDING_PROVIDER"
        )
    if _prov_override:
        provider = _prov_override
    # Normalize alias
    if provider == "azure":
        provider = "azure-openai"
    # Small debug to help ops understand where REDIS_URL was sourced from (no credentials included)
    try:
        _p = urlparse(redis_url)
        _host = _p.hostname or "?"
        _port = _p.port
        _scheme = _p.scheme
        _safe = f"{_scheme}://{_host}{(':' + str(_port)) if _port else ''}"
    except Exception:
        _safe = "(unparsed)"
    log.info(
        "Initializing semantic cache",
        redis_source=_redis_source,
        redis_host=_safe,
        embedding_provider=provider,
    )
    init_semantic_cache(redis_url=redis_url, embedding_provider=provider, cfg=cfg)
