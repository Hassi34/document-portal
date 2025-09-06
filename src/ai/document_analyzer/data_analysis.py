import os
import sys
from typing import Any, Dict

from pydantic import BaseModel

from src.ai.parsing.output_parsing import (
    build_structured_chain,
    get_pydantic_parser,
)
from src.ai.prompt.prompt_library import PROMPT_REGISTRY  # type: ignore
from src.schemas.ai.models import Metadata
from src.utils.exception.custom_exception import DocumentPortalException
from src.utils.logger import GLOBAL_LOGGER as log
from src.utils.model_loader import ModelLoader


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
            raw = self.chain.invoke(
                {
                    "format_instructions": self.pyd_parser.get_format_instructions(),
                    "document_text": document_text,
                }
            )
            response = self._normalize_to_dict(raw)
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
