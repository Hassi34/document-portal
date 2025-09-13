import os
import sys
from operator import itemgetter
from typing import Any, Dict, List

from langchain_community.vectorstores import FAISS
from langchain_core.messages import BaseMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from src.ai.prompt.prompt_library import PROMPT_REGISTRY
from src.schemas.ai.models import PromptType
from src.utils.config_loader import load_config
from src.utils.exception.custom_exception import DocumentPortalException
from src.utils.logger import GLOBAL_LOGGER as log
from src.utils.model_loader import ModelLoader

"""Conversational RAG chain (LCEL) with optional callback support for Langfuse.

Instrumentation is now expected to be performed at the API layer using
`langfuse.decorators.observe` and the LangChain handler callbacks rather than
direct custom trace/span helpers inside this class. This keeps the LCEL graph
comprised only of Runnable-compatible components.
"""


class ConversationalRAG:
    """LCEL-based Conversational RAG with lazy retriever initialization.

    Usage:
        rag = ConversationalRAG(session_id="abc")
        rag.load_retriever_from_faiss(index_path="faiss_index/abc", k=5, index_name="index")
        answer = rag.invoke("What is ...?", chat_history=[])
    """

    def __init__(self, session_id: str | None, retriever=None):
        try:
            self.session_id = session_id
            self.cfg = load_config()

            # Load LLM and prompts once
            self.llm = self._load_llm()
            self.contextualize_prompt: ChatPromptTemplate = PROMPT_REGISTRY[
                PromptType.CONTEXTUALIZE_QUESTION.value
            ]
            self.qa_prompt: ChatPromptTemplate = PROMPT_REGISTRY[
                PromptType.CONTEXT_QA.value
            ]

            # Lazy pieces
            self.retriever = retriever
            self.chain = None
            if self.retriever is not None:
                self._build_lcel_chain()

            log.info("ConversationalRAG initialized", session_id=self.session_id)
        except Exception as e:
            log.error("Failed to initialize ConversationalRAG", error=str(e))
            raise DocumentPortalException(
                "Initialization error in ConversationalRAG", sys
            )

    # ---------- Public API ----------

    def load_retriever_from_faiss(
        self,
        index_path: str,
        k: int | None = None,
        index_name: str | None = None,
        search_type: str | None = None,
        search_kwargs: Dict[str, Any] | None = None,
    ):
        """Load FAISS vectorstore from disk and build retriever + LCEL chain.

        Falls back to config for defaults when arguments aren't provided.
        """
        try:
            if not os.path.isdir(index_path):
                raise FileNotFoundError(
                    f"FAISS index directory not found: {index_path}"
                )

            embeddings = ModelLoader().load_embeddings()
            # Resolve defaults from cached config
            if not index_name:
                index_name = (
                    self.cfg.get("ai", {})
                    .get("vector_db", {})
                    .get("faiss", {})
                    .get("index_name", "index")
                )
            if k is None:
                k = int(self.cfg.get("ai", {}).get("retriever", {}).get("top_k", 10))
            if search_type is None:
                search_type = (
                    self.cfg.get("ai", {})
                    .get("retriever", {})
                    .get("search_type", "similarity")
                )

            vectorstore = FAISS.load_local(
                index_path,
                embeddings,
                index_name=index_name,
                allow_dangerous_deserialization=True,  # ok if you trust the index
            )

            # Merge k into search_kwargs without clobbering provided keys
            if search_kwargs is None:
                search_kwargs = {"k": k}
            else:
                search_kwargs = {"k": k, **search_kwargs}

            self.retriever = vectorstore.as_retriever(
                search_type=search_type, search_kwargs=search_kwargs
            )
            self._build_lcel_chain()

            log.info(
                "FAISS retriever loaded successfully",
                index_path=index_path,
                index_name=index_name,
                k=k,
                session_id=self.session_id,
            )
            return self.retriever

        except Exception as e:
            log.error("Failed to load retriever from FAISS", error=str(e))
            raise DocumentPortalException("Loading error in ConversationalRAG", sys)

    def invoke(
        self,
        user_input: str,
        chat_history: List[BaseMessage] | None = None,
        callbacks: List[Any] | None = None,
    ) -> str:
        """Invoke the LCEL chain.

        callbacks: optional LangChain callback handlers (e.g., Langfuse handler)
        passed via API layer instrumentation.
        """
        try:
            if self.chain is None:
                raise DocumentPortalException(
                    "RAG chain not initialized. Call load_retriever_from_faiss() before invoke().",
                    sys,
                )
            chat_history = chat_history or []
            payload = {"input": user_input, "chat_history": chat_history}
            run_config = {"callbacks": callbacks} if callbacks else None
            answer = self.chain.invoke(payload, config=run_config)  # type: ignore[arg-type]
            if not answer:
                log.warning(
                    "No answer generated",
                    user_input=user_input,
                    session_id=self.session_id,
                )
                return "no answer generated."
            log.info(
                "Chain invoked successfully",
                session_id=self.session_id,
                user_input=user_input,
                answer_preview=str(answer)[:150],
            )
            return str(answer)
        except Exception as e:
            log.error("Failed to invoke ConversationalRAG", error=str(e))
            raise DocumentPortalException("Invocation error in ConversationalRAG", sys)

    # ---------- Internals ----------

    def _load_llm(self):
        try:
            llm = ModelLoader().load_llm()
            if not llm:
                raise ValueError("LLM could not be loaded")
            log.info("LLM loaded successfully", session_id=self.session_id)
            return llm
        except Exception as e:
            log.error("Failed to load LLM", error=str(e))
            raise DocumentPortalException("LLM loading error in ConversationalRAG", sys)

    @staticmethod
    def _format_docs(docs) -> str:
        """Format retrieved documents into a single context string."""
        return "\n\n".join(getattr(d, "page_content", str(d)) for d in docs)

    def _build_lcel_chain(self):
        try:
            if self.retriever is None:
                raise DocumentPortalException(
                    "No retriever set before building chain", sys
                )

            # 1) Rewrite user question with chat history context
            question_rewriter = (
                {
                    "input": itemgetter("input"),
                    "chat_history": itemgetter("chat_history"),
                }
                | self.contextualize_prompt
                | self.llm
                | StrOutputParser()
            )

            # 2) Retrieve docs for rewritten question
            retrieve_docs = question_rewriter | self.retriever | self._format_docs

            # 3) Answer using retrieved context + original input + chat history
            self.chain = (
                {
                    "context": retrieve_docs,
                    "input": itemgetter("input"),
                    "chat_history": itemgetter("chat_history"),
                }
                | self.qa_prompt
                | self.llm
                | StrOutputParser()
            )

            log.info("LCEL graph built successfully", session_id=self.session_id)
        except Exception as e:
            log.error(
                "Failed to build LCEL chain", error=str(e), session_id=self.session_id
            )
            raise DocumentPortalException("Failed to build LCEL chain", sys)

    # ---------- Convenience ----------

    @property
    def is_ready(self) -> bool:
        """Return True if the retriever and chain are ready for invocation."""
        return self.retriever is not None and self.chain is not None

    def clear(self) -> None:
        """Clear retriever and chain to free resources or reinitialize later."""
        self.retriever = None
        self.chain = None
