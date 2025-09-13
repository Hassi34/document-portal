import os
import sys
from typing import Any, Dict

from langfuse import get_client  # type: ignore
from langfuse.langchain import CallbackHandler  # type: ignore
from pydantic import BaseModel

from src.ai.parsing.output_parsing import (
    build_structured_chain,
    get_pydantic_parser,
)
from src.ai.prompt.prompt_library import PROMPT_REGISTRY  # type: ignore
from src.schemas.ai.models import Metadata
from llm_observability.src.tracing import record_analysis
from src.utils.exception.custom_exception import DocumentPortalException
from src.utils.logger import GLOBAL_LOGGER as log
from src.utils.model_loader import ModelLoader
from src.utils.token_counter import count_tokens


class DocumentAnalyzer:
    """
    Analyzes documents using a pre-trained model.
    Automatically logs all actions and supports session-based organization.
    """

    def __init__(self):
        try:
            self.loader = ModelLoader()
            self.llm = self.loader.load_llm()

            # Prepare prompt, parser, and robust chain once
            self.prompt = PROMPT_REGISTRY["document_analysis"]
            self.pyd_parser = get_pydantic_parser(Metadata)
            self.chain = build_structured_chain(self.prompt, self.llm, Metadata)

            log.info("DocumentAnalyzer initialized successfully")

        except Exception as e:
            log.error(f"Error initializing DocumentAnalyzer: {e}")
            raise DocumentPortalException(
                "Error in DocumentAnalyzer initialization", sys
            )

    def _normalize_to_dict(self, raw: Any) -> Dict[str, Any]:
        """Normalize chain output to a plain dict for consistent API responses."""
        if isinstance(raw, BaseModel):
            return raw.model_dump()
        if isinstance(raw, dict):
            return raw
        if hasattr(raw, "model_dump"):
            try:
                return raw.model_dump()  # type: ignore[attr-defined]
            except Exception:
                pass
        try:
            return dict(raw)  # type: ignore[arg-type]
        except Exception:
            return {"value": str(raw)}

    def analyze_document(self, document_text: str) -> dict:
        """
        Analyze a document's text and extract structured metadata & summary.
        """
        try:
            log.info("Meta-data analysis chain initialized")
            # Try to bind current Langfuse LangChain handler so this run is captured
            run_inputs = {
                "format_instructions": self.pyd_parser.get_format_instructions(),
                "document_text": document_text,
            }
            try:
                handler = CallbackHandler()
            except Exception:
                handler = None
            # Pre-update current generation for automatic cost inference
            try:
                client = get_client()
                if client and hasattr(client, "update_current_generation"):
                    client.update_current_generation(
                        input=document_text,
                        model=getattr(self.llm, "_dp_model_name", None)
                        or "unknown-model",
                        metadata={"flow": "document_analysis"},
                    )
            except Exception:
                log.warning("FAILED TO UPDATE CURRENT GENERATION INPUT/MODEL")
            if handler:
                raw = self.chain.invoke(run_inputs, config={"callbacks": [handler]})
            else:
                log.warning("NO LANGFUSE HANDLER AVAILABLE; RUNNING WITHOUT CALLBACKS")
                raw = self.chain.invoke(run_inputs)
            response = self._normalize_to_dict(raw)
            # Post-update usage_details
            try:
                client = get_client()
                provider = os.getenv(
                    "CHAT_PROVIDER", os.getenv("LLM_PROVIDER", "openai")
                )
                model_name = (
                    getattr(self.llm, "_dp_model_name", None) or "unknown-model"
                )
                in_toks = count_tokens(provider, model_name, document_text)
                out_toks = count_tokens(provider, model_name, str(response))
                if client and hasattr(client, "update_current_generation"):
                    client.update_current_generation(
                        usage_details={
                            "input": in_toks,
                            "output": out_toks,
                        }
                    )
            except Exception:
                log.warning("FAILED TO UPDATE CURRENT GENERATION USAGE DETAILS")
            # Record usage via observed helper
            try:
                provider = os.getenv(
                    "CHAT_PROVIDER", os.getenv("LLM_PROVIDER", "openai")
                )
                model_name = (
                    getattr(self.llm, "_dp_model_name", None) or "unknown-model"
                )
                record_analysis(
                    model=model_name,
                    provider=provider,
                    input_snippet=document_text,
                    output_snippet=str(response),
                )
            except Exception:
                pass
            keys = []
            if isinstance(response, dict):
                try:
                    keys = list(response.keys())
                except Exception:
                    keys = []
            log.info(f"Metadata extraction successful; keys={keys}")
            return response
        except Exception as e:
            log.error(f"Metadata analysis failed: {e}")
            raise DocumentPortalException("Metadata extraction failed", sys)
