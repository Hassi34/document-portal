import os
import sys

import pandas as pd
from dotenv import load_dotenv
from langchain_core.output_parsers import JsonOutputParser
from langfuse import get_client  # type: ignore
from langfuse.langchain import CallbackHandler  # type: ignore
from pydantic import BaseModel

from src.ai.parsing.output_parsing import (
    build_structured_chain,
    get_pydantic_parser,
)
from src.ai.prompt.prompt_library import PROMPT_REGISTRY
from src.schemas.ai.models import PromptType, SummaryResponse
from llm_observability.src.tracing import record_comparison
from src.utils.exception.custom_exception import DocumentPortalException
from src.utils.logger import GLOBAL_LOGGER as log
from src.utils.model_loader import ModelLoader


class DocumentComparatorLLM:
    def __init__(self):
        load_dotenv()
        self.loader = ModelLoader()
        self.llm = self.loader.load_llm()
        self.parser = JsonOutputParser(pydantic_object=SummaryResponse)
        self.prompt = PROMPT_REGISTRY[PromptType.DOCUMENT_COMPARISON.value]

        self._pyd_parser = get_pydantic_parser(SummaryResponse)
        self.chain = build_structured_chain(
            self.prompt,
            self.llm,
            SummaryResponse,
            format_instruction_key="format_instruction",
        )
        log.info("DocumentComparatorLLM initialized", model=self.llm)

    def compare_documents(self, combined_docs: str) -> pd.DataFrame:
        try:
            inputs = {
                "combined_docs": combined_docs,
                "format_instruction": self._pyd_parser.get_format_instructions(),
            }

            log.info("Invoking document comparison LLM chain")
            # Bind Langfuse LangChain handler via new pattern
            try:
                handler = CallbackHandler()
            except Exception:
                handler = None
            # Pre-update current generation for automatic cost inference
            try:
                client = get_client()
                if client and hasattr(client, "update_current_generation"):
                    client.update_current_generation(
                        input=combined_docs,
                        model=getattr(self.llm, "_dp_model_name", None)
                        or "unknown-model",
                        metadata={"flow": "document_comparison"},
                    )
            except Exception:
                log.warning("FAILED TO UPDATE CURRENT GENERATION INPUT/MODEL")
            if handler:
                raw = self.chain.invoke(inputs, config={"callbacks": [handler]})
            else:
                log.warning("NO LANGFUSE HANDLER AVAILABLE; RUNNING WITHOUT CALLBACKS")
                raw = self.chain.invoke(inputs)
            log.info("Chain invoked successfully", response_preview=str(raw)[:200])

            # Normalize to list[dict] for API schema compatibility
            data: list = []
            if isinstance(raw, BaseModel):
                dumped = raw.model_dump()
                if isinstance(dumped, dict) and "root" in dumped:
                    data = dumped["root"]
                elif isinstance(dumped, list):
                    data = dumped
                else:
                    data = [dumped]
            elif isinstance(raw, list):
                data = raw
            elif isinstance(raw, dict):
                if "root" in raw and isinstance(raw["root"], list):
                    data = raw["root"]
                else:
                    # Possibly a single row dict
                    data = [raw]
            else:
                data = [raw]

            # Ensure each row is a dict
            rows: list[dict] = []
            for item in data:
                if isinstance(item, BaseModel):
                    rows.append(item.model_dump())
                elif isinstance(item, dict):
                    rows.append(item)
                else:
                    try:
                        rows.append(dict(item))  # type: ignore[arg-type]
                    except Exception:
                        rows.append({"value": str(item)})

            df = self._format_response(rows)
            # Post-update usage_details and record usage via observed helper
            try:
                client = get_client()
                provider = os.getenv(
                    "CHAT_PROVIDER", os.getenv("LLM_PROVIDER", "openai")
                )
                model_name = (
                    getattr(self.llm, "_dp_model_name", None) or "unknown-model"
                )
                # Update usage_details for Langfuse
                if client and hasattr(client, "update_current_generation"):
                    from src.utils.token_counter import count_tokens as _ct

                    in_toks = _ct(provider, model_name, combined_docs)
                    out_toks = _ct(provider, model_name, df.to_json())
                    client.update_current_generation(
                        usage_details={
                            "input": in_toks,
                            "output": out_toks,
                        }
                    )
                # Also log via our observed helper
                record_comparison(
                    model=model_name,
                    provider=provider,
                    left=combined_docs,
                    right="",
                    result_text=df.to_json(),
                )
            except Exception:
                pass
            return df
        except Exception as e:
            log.error("Error in compare_documents", error=str(e))
            raise DocumentPortalException("Error comparing documents", sys)

    def _format_response(self, rows: list[dict]) -> pd.DataFrame:  # type: ignore
        try:
            df = pd.DataFrame(rows)
            return df
        except Exception as e:
            log.error("Error formatting response into DataFrame", error=str(e))
            DocumentPortalException("Error formatting response", sys)
