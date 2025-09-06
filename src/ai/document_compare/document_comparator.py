import sys

import pandas as pd
from dotenv import load_dotenv
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel

from src.ai.parsing.output_parsing import (
    build_structured_chain,
    get_pydantic_parser,
)
from src.ai.prompt.prompt_library import PROMPT_REGISTRY
from src.schemas.ai.models import PromptType, SummaryResponse
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

            return self._format_response(rows)
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
