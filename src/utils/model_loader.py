"""Helpers to load embeddings and LLMs based on config + environment."""

import os
import sys
from typing import Any

from dotenv import load_dotenv
from src.utils.config_loader import load_config
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_groq import ChatGroq
from langchain_openai import (
    ChatOpenAI,
    OpenAIEmbeddings,
    AzureChatOpenAI,
    AzureOpenAIEmbeddings,
)
from src.utils.logger.custom_logging import CustomLogger
from src.utils.exception.custom_exception import DocumentPortalException

log = CustomLogger().get_logger(__name__)

class ModelLoader:
    """Load embedding and LLM models configured via YAML and env vars."""

    def __init__(self) -> None:
        load_dotenv()
        self._validate_env()
        self.config = load_config()
        log.info("Configuration loaded successfully", config_keys=list(self.config.keys()))
        
    def _validate_env(self) -> None:
        """Validate required API keys and cache them into self.api_keys.

        Includes optional Azure OpenAI variables when provider is selected.
        """
        # Global required keys (now includes Azure key as requested)
        required_vars = [
            "GOOGLE_API_KEY",
            "GROQ_API_KEY",
            "OPENAI_API_KEY",
            "AZURE_OPENAI_API_KEY",
        ]
        # Azure-specific vars (required when provider is azure)
        azure_vars = [
            "AZURE_OPENAI_API_INSTANCE_NAME",
            "AZURE_OPENAI_API_DEPLOYMENT_NAME",
            "AZURE_OPENAI_API_VERSION",
        ]
        self.api_keys = {key: os.getenv(key) for key in required_vars + azure_vars}
        missing_required = [k for k in required_vars if not self.api_keys.get(k)]
        if missing_required:
            log.error("Missing required environment variables", missing_vars=missing_required)
            raise DocumentPortalException("Missing required environment variables", sys)
        # Provider-specific validation
        provider_embedding = os.getenv("EMBEDDING_PROVIDER", "openai").lower()
        provider_llm = os.getenv("LLM_PROVIDER", "openai").lower()
        if provider_embedding in ("azure", "azure-openai") or provider_llm in ("azure", "azure-openai"):
            for k in azure_vars:
                if not self.api_keys.get(k):
                    log.error("Missing Azure OpenAI environment variable", var=k)
                    raise DocumentPortalException("Missing Azure OpenAI environment variables", sys)
        # Validate base providers when chosen
        if provider_embedding == "openai" and not self.api_keys.get("OPENAI_API_KEY"):
            raise DocumentPortalException("Missing OPENAI_API_KEY for embeddings", sys)
        if provider_llm == "openai" and not self.api_keys.get("OPENAI_API_KEY"):
            raise DocumentPortalException("Missing OPENAI_API_KEY for LLM", sys)
        log.info("Environment variables validated", available_keys=[k for k in self.api_keys if self.api_keys[k]])
        
    def load_embeddings(self):
        """Load and return the embedding model based on provider configuration."""
        try:
            log.info("Loading embedding model...")
            
            embedding_block = self.config["ai"]["embedding_model"]
            
            provider_key = os.getenv("EMBEDDING_PROVIDER", "openai")  # Default openai
            # Back-compat: allow 'azure' but prefer 'azure-openai' config key
            if provider_key == "azure":
                provider_key = "azure-openai"
            if provider_key not in embedding_block:
                log.error("Embedding provider not found in config", provider_key=provider_key)
                raise ValueError(f"Embedding provider '{provider_key}' not found in config")

            embedding_config = embedding_block[provider_key]
            model_name = embedding_config.get("model_name")
            
            log.info("Loading embedding model", provider=provider_key, model=model_name)

            if provider_key == "google":
                return GoogleGenerativeAIEmbeddings(
                    model=model_name,
                    google_api_key=self.api_keys["GOOGLE_API_KEY"]
                )
            elif provider_key == "openai":
                return OpenAIEmbeddings(
                    model=model_name,
                    openai_api_key=self.api_keys["OPENAI_API_KEY"]
                )
            elif provider_key == "azure-openai":
                # For Azure embeddings, you must set a dedicated embedding deployment
                deployment = os.getenv("AZURE_OPENAI_API_EMBEDDING_DEPLOYMENT_NAME")
                if not deployment:
                    log.error(
                        "Azure embedding deployment name not set",
                        env_var="AZURE_OPENAI_API_EMBEDDING_DEPLOYMENT_NAME",
                    )
                    raise DocumentPortalException("Missing Azure embedding deployment env", sys)
                api_key = self.api_keys["AZURE_OPENAI_API_KEY"]
                instance = self.api_keys["AZURE_OPENAI_API_INSTANCE_NAME"]
                api_version = self.api_keys["AZURE_OPENAI_API_VERSION"]
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
        
        provider_key = os.getenv("LLM_PROVIDER", "openai")  # Default openai
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
        
        log.info("Loading LLM", provider=provider_key, model=model_name, temperature=temperature, max_tokens=max_tokens)

        if provider_key == "google":
            llm = ChatGoogleGenerativeAI(
                model=model_name,
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
            return llm

        elif provider_key == "groq":
            llm = ChatGroq(
                model=model_name,
                api_key=self.api_keys["GROQ_API_KEY"],
                temperature=temperature,
            )
            return llm

        elif provider_key == "openai":
            return ChatOpenAI(
                model=model_name,
                api_key=self.api_keys["OPENAI_API_KEY"],
                temperature=temperature,
                max_tokens=max_tokens,
            )

        elif provider_key == "azure-openai":
            # Azure OpenAI chat via Azure-specific wrapper per docs
            api_key = self.api_keys["AZURE_OPENAI_API_KEY"]
            instance = self.api_keys["AZURE_OPENAI_API_INSTANCE_NAME"]
            deployment = self.api_keys["AZURE_OPENAI_API_DEPLOYMENT_NAME"]
            api_version = self.api_keys["AZURE_OPENAI_API_VERSION"]
            azure_endpoint = f"https://{instance}.openai.azure.com/"
            return AzureChatOpenAI(
                azure_endpoint=azure_endpoint,
                azure_deployment=deployment,
                openai_api_version=api_version,
                openai_api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        else:
            log.error("Unsupported LLM provider", provider=provider_key)
            raise ValueError(f"Unsupported LLM provider: {provider_key}")
        
    
    
if __name__ == "__main__":
    loader = ModelLoader()
    
    # Test embedding model loading
    embeddings = loader.load_embeddings()
    print(f"Embedding Model Loaded: {embeddings}")
    
    # Test the ModelLoader
    result=embeddings.embed_query("Hello, how are you?")
    print(f"Embedding Result: {result}")
    
    # Test LLM loading based on YAML config
    llm = loader.load_llm()
    print(f"LLM Loaded: {llm}")
    
    # Test the ModelLoader
    result=llm.invoke("Hello, how are you?")
    print(f"LLM Result: {result.content}")