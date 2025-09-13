"""Helpers to load embeddings and LLMs based on config + environment."""

import json
import os
import sys
from typing import List

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_groq import ChatGroq
from langchain_openai import (
    AzureChatOpenAI,
    AzureOpenAIEmbeddings,
    ChatOpenAI,
    OpenAIEmbeddings,
)

from src.utils.config_loader import load_config
from src.utils.exception.custom_exception import DocumentPortalException
from src.utils.logger.custom_logging import CustomLogger


def maybe_wrap_llm(
    llm, provider: str, model: str
):  # deprecated no-op retained for compatibility
    return llm


log = CustomLogger().get_logger(__name__)


class ApiKeyManager:
    """Centralized API key/env var loader with JSON bundle support.

    Reads API_KEYS (or configured name) as JSON, and falls back to individual env vars for
    the configured known keys from YAML. Keeps fallbacks minimal, per guidance.
    """

    def __init__(self) -> None:
        cfg = load_config()
        secrets_cfg = cfg.get("secrets", {})
        self._known_keys = secrets_cfg.get("known_keys", [])
        env_name = secrets_cfg.get("aws_secret_manager_keys_env_var", "API_KEYS")

        self._store = {}
        raw = os.getenv(env_name)
        if raw:
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise ValueError("API keys env is not a valid JSON object")
                for k, v in parsed.items():
                    if isinstance(k, str) and isinstance(v, str | int | float):
                        self._store[k] = str(v)
                log.info("Loaded API keys from JSON env", env_var=env_name)
            except Exception as e:
                log.warning(
                    "Failed to parse API keys JSON", env_var=env_name, error=str(e)
                )

        # Minimal fallback: individual env vars for configured keys only
        for key in self._known_keys:
            if key not in self._store and os.getenv(key):
                self._store[key] = os.getenv(key, "")

        masked = {k: (v[:6] + "..." if v else "") for k, v in self._store.items() if v}
        if masked:
            log.info("API keys loaded", keys=masked)

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._store.get(key, os.getenv(key, default))

    def require(self, keys: str | List[str]) -> str | None:
        """Ensure required env var(s) exist.

        - If a single key (str) is provided, return its value on success.
        - If a list is provided, validate all exist and return None.
        """
        if isinstance(keys, str):
            val = self.get(keys)
            if not val:
                log.error("Missing required key", missing_key=keys)
                raise DocumentPortalException(
                    f"Missing required API key/var: {keys}", sys
                )
            return val

        missing = [k for k in keys if not self.get(k)]
        if missing:
            log.error("Missing required keys", missing_keys=missing)
            raise DocumentPortalException("Missing required API keys/vars", sys)
        return None


class ModelLoader:
    """Load embedding and LLM models configured via YAML and env vars."""

    def __init__(self) -> None:
        # Only load .env locally; in prod rely on env/Secrets
        if os.getenv("ENV", "local").lower() != "production":
            load_dotenv()
            log.info("Running in LOCAL mode: .env loaded")
        else:
            log.info("Running in PRODUCTION mode")

        self.api_keys = ApiKeyManager()
        self.config = load_config()
        log.info(
            "Configuration loaded successfully", config_keys=list(self.config.keys())
        )

    def load_embeddings(self):
        """Load and return the embedding model based on provider configuration."""
        try:
            log.info("Loading embedding model...")

            embedding_block = self.config["ai"]["embedding_model"]

            provider_key = self.api_keys.require("EMBEDDING_PROVIDER")
            # Back-compat: allow 'azure' but prefer 'azure-openai' config key
            if provider_key == "azure":
                provider_key = "azure-openai"
            if provider_key not in embedding_block:
                log.error(
                    "Embedding provider not found in config", provider_key=provider_key
                )
                raise ValueError(
                    f"Embedding provider '{provider_key}' not found in config"
                )

            embedding_config = embedding_block[provider_key]
            model_name = embedding_config.get("model_name")

            log.info("Loading embedding model", provider=provider_key, model=model_name)

            if provider_key == "google":
                self.api_keys.require(["GOOGLE_API_KEY"])
                return GoogleGenerativeAIEmbeddings(
                    model=model_name, google_api_key=self.api_keys.get("GOOGLE_API_KEY")
                )
            elif provider_key == "openai":
                self.api_keys.require(["OPENAI_API_KEY"])
                return OpenAIEmbeddings(
                    model=model_name, openai_api_key=self.api_keys.get("OPENAI_API_KEY")
                )
            elif provider_key == "azure-openai":
                # For Azure embeddings, you must set a dedicated embedding deployment
                self.api_keys.require(
                    [
                        "AZURE_OPENAI_API_KEY",
                        "AZURE_OPENAI_API_INSTANCE_NAME",
                        "AZURE_OPENAI_API_VERSION",
                        "AZURE_OPENAI_API_EMBEDDING_DEPLOYMENT_NAME",
                    ]
                )
                deployment = self.api_keys.get(
                    "AZURE_OPENAI_API_EMBEDDING_DEPLOYMENT_NAME"
                )
                api_key = self.api_keys.get("AZURE_OPENAI_API_KEY")
                instance = self.api_keys.get("AZURE_OPENAI_API_INSTANCE_NAME")
                api_version = self.api_keys.get("AZURE_OPENAI_API_VERSION")
                azure_endpoint = f"https://{instance}.openai.azure.com/"
                # Use Azure-specific embeddings wrapper per docs
                return AzureOpenAIEmbeddings(
                    model=model_name,
                    azure_endpoint=azure_endpoint,
                    azure_deployment=deployment,
                    openai_api_version=api_version,
                    api_key=api_key,
                )
            else:
                log.error("Unsupported embedding provider", provider=provider_key)
                raise ValueError(f"Unsupported embedding provider: {provider_key}")

        except Exception as e:
            log.error("Error loading embedding model", error=str(e))
            raise DocumentPortalException("Failed to load embedding model", sys)

    def load_llm(self):
        """Load and return the LLM model based on provider configuration."""

        llm_block = self.config["ai"]["llm"]

        log.info("Loading LLM...")

        provider_key = self.api_keys.require("LLM_PROVIDER")
        # Back-compat: allow 'azure' but prefer 'azure-openai' config key
        if provider_key == "azure":
            provider_key = "azure-openai"
        if provider_key not in llm_block:
            log.error("LLM provider not found in config", provider_key=provider_key)
            raise ValueError(f"Provider '{provider_key}' not found in config")

        llm_config = llm_block[provider_key]
        model_name = llm_config.get("model_name")
        temperature = llm_config.get("temperature", 0.2)
        max_tokens = llm_config.get("max_output_tokens", 2048)

        log.info(
            "Loading LLM",
            provider=provider_key,
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        if provider_key == "google":
            self.api_keys.require(["GOOGLE_API_KEY"])
            llm = ChatGoogleGenerativeAI(
                model=model_name,
                temperature=temperature,
                max_output_tokens=max_tokens,
                google_api_key=self.api_keys.get("GOOGLE_API_KEY"),
            )
            # attach metadata
            setattr(llm, "_dp_provider", provider_key)
            setattr(llm, "_dp_model_name", model_name)
            return llm

        elif provider_key == "groq":
            self.api_keys.require(["GROQ_API_KEY"])
            llm = ChatGroq(
                model=model_name,
                api_key=self.api_keys.get("GROQ_API_KEY"),
                temperature=temperature,
            )
            setattr(llm, "_dp_provider", provider_key)
            setattr(llm, "_dp_model_name", model_name)
            return llm

        elif provider_key == "openai":
            self.api_keys.require(["OPENAI_API_KEY"])
            llm = ChatOpenAI(
                model=model_name,
                api_key=self.api_keys.get("OPENAI_API_KEY"),
                temperature=temperature,
                max_tokens=max_tokens,
            )
            setattr(llm, "_dp_provider", provider_key)
            setattr(llm, "_dp_model_name", model_name)
            return llm

        elif provider_key == "azure-openai":
            # Azure OpenAI chat via Azure-specific wrapper per docs
            self.api_keys.require(
                [
                    "AZURE_OPENAI_API_KEY",
                    "AZURE_OPENAI_API_INSTANCE_NAME",
                    "AZURE_OPENAI_API_DEPLOYMENT_NAME",
                    "AZURE_OPENAI_API_VERSION",
                ]
            )
            api_key = self.api_keys.get("AZURE_OPENAI_API_KEY")
            instance = self.api_keys.get("AZURE_OPENAI_API_INSTANCE_NAME")
            deployment = self.api_keys.get("AZURE_OPENAI_API_DEPLOYMENT_NAME")
            api_version = self.api_keys.get("AZURE_OPENAI_API_VERSION")
            azure_endpoint = f"https://{instance}.openai.azure.com/"
            llm = AzureChatOpenAI(
                azure_endpoint=azure_endpoint,
                azure_deployment=deployment,
                openai_api_version=api_version,
                openai_api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            setattr(llm, "_dp_provider", provider_key)
            setattr(llm, "_dp_model_name", model_name)
            return llm

        else:
            log.error("Unsupported LLM provider", provider=provider_key)
            raise ValueError(f"Unsupported LLM provider: {provider_key}")


if __name__ == "__main__":
    loader = ModelLoader()

    # Test embedding model loading
    embeddings = loader.load_embeddings()
    print(f"Embedding Model Loaded: {embeddings}")

    # Test the ModelLoader
    result = embeddings.embed_query("Hello, how are you?")
    print(f"Embedding Result: {result}")

    # Test LLM loading based on YAML config
    llm = loader.load_llm()
    print(f"LLM Loaded: {llm}")

    # Test the ModelLoader
    result = llm.invoke("Hello, how are you?")
    print(f"LLM Result: {result.content}")
