"""Utilities for robust structured output parsing with LangChain.

Provides helpers to:
- create a PydanticOutputParser for a given schema
- wrap with OutputFixingParser
- optionally retry with RetryOutputParser using the original prompt
- build a ready LCEL chain: prompt | llm | (fixing parser | base parser)

Behavior is config-driven via configs/config.yaml under ai.output_parsing.
"""

from __future__ import annotations

from typing import Any, Optional, Type

from langchain.output_parsers import OutputFixingParser, RetryOutputParser
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import BasePromptTemplate
from langchain_core.runnables import Runnable, RunnableLambda

from src.utils.config_loader import load_config


def get_pydantic_parser(schema: Type[Any]) -> PydanticOutputParser:
    return PydanticOutputParser(pydantic_object=schema)


def wrap_with_fixer(parser: PydanticOutputParser, llm: Any) -> OutputFixingParser:
    return OutputFixingParser.from_llm(parser=parser, llm=llm)


def get_retry_parser(parser: PydanticOutputParser, llm: Any) -> RetryOutputParser:
    return RetryOutputParser.from_llm(parser=parser, llm=llm)


def build_structured_chain(
    prompt: BasePromptTemplate,
    llm: Any,
    schema: Type[Any],
    *,
    format_instruction_key: str = "format_instructions",
) -> Runnable:
    """Return a Runnable chain that parses into a Pydantic schema robustly.

    Reads ai.output_parsing config:
      - enable_fix: wrap with OutputFixingParser
      - enable_retry: on failure, retry using RetryOutputParser.parse_with_prompt
      - retry_max_attempts: retry attempts (default 1)

    The returned chain expects the caller to pass the variable that the prompt
    requires plus the format_instructions key matching `format_instruction_key`.
    """

    cfg = load_config()
    op_cfg = (
        cfg.get("ai", {}).get("output_parsing", {}) if isinstance(cfg, dict) else {}
    )
    enable_fix: bool = bool(op_cfg.get("enable_fix", True))
    enable_retry: bool = bool(op_cfg.get("enable_retry", True))
    retry_max: int = int(op_cfg.get("retry_max_attempts", 1))

    base_parser = get_pydantic_parser(schema)
    parser_for_chain = wrap_with_fixer(base_parser, llm) if enable_fix else base_parser

    # Compose default chain: prompt | llm | parser
    chain = prompt | llm | parser_for_chain

    if not enable_retry:
        return chain

    # Add a post-step retry using RetryOutputParser if parsing failed.
    retry_parser = get_retry_parser(base_parser, llm)

    def _invoke_with_retry(inputs: dict) -> Any:
        # Ensure format instructions are present for the base parser
        inputs = dict(inputs)
        inputs.setdefault(format_instruction_key, base_parser.get_format_instructions())

        try:
            return chain.invoke(inputs)
        except Exception:
            # Build the original prompt value to supply to parse_with_prompt
            prompt_value = prompt.format_prompt(**inputs)
            last_err: Optional[Exception] = None
            for _ in range(max(1, retry_max)):
                try:
                    # Requires the raw LLM text; here we re-ask the model via prompt | llm
                    completion = (prompt | llm).invoke(inputs)
                    # Retry parser uses the erroneous output with the prompt; pass completion
                    return retry_parser.parse_with_prompt(completion, prompt_value)
                except Exception as e:  # noqa: PERF203 - intentional broad catch for retry loop
                    last_err = e
            if last_err:
                raise last_err
            raise

    return RunnableLambda(_invoke_with_retry)
