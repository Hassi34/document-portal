from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from langfuse import observe  # type: ignore

from src.ai.document_compare.document_comparator import DocumentComparatorLLM
from src.ai.document_ingestion.data_ingestion import DocumentComparator
from src.schemas.api.ouput import CompareResponse
from src.utils.document_ops import FastAPIFileAdapter
from src.utils.logger import GLOBAL_LOGGER as log

router = APIRouter(prefix="/compare", tags=["compare"])


@router.post("", response_model=CompareResponse)
@observe()
async def compare_documents(
    reference: UploadFile = File(...), actual: UploadFile = File(...)
) -> Any:
    try:
        log.info(f"Comparing files: {reference.filename} vs {actual.filename}")
        dc = DocumentComparator()
        ref_path, act_path = dc.save_uploaded_files(
            FastAPIFileAdapter(reference), FastAPIFileAdapter(actual)
        )
        _ = ref_path, act_path
        combined_text = dc.combine_documents()
        comp = DocumentComparatorLLM()
        df = comp.compare_documents(combined_text)
        log.info("Document comparison completed.")
        return {"rows": df.to_dict(orient="records"), "session_id": dc.session_id}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Comparison failed")
        raise HTTPException(status_code=500, detail=f"Comparison failed: {e}")
