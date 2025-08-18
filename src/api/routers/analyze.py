from typing import Any
from fastapi import APIRouter, UploadFile, File, HTTPException
from src.ai.document_ingestion.data_ingestion import DocHandler
from src.ai.document_analyzer.data_analysis import DocumentAnalyzer
from src.utils.document_ops import FastAPIFileAdapter, read_pdf_via_handler
from src.utils.logger import GLOBAL_LOGGER as log
from src.schemas.api.ouput import AnalyzeResponse

router = APIRouter(prefix="/analyze", tags=["analyze"])


@router.post("", response_model=AnalyzeResponse)
async def analyze_document(file: UploadFile = File(...)) -> Any:
    try:
        log.info(f"Received file for analysis: {file.filename}")
        dh = DocHandler()
        saved_path = dh.save_pdf(FastAPIFileAdapter(file))
        text = read_pdf_via_handler(dh, saved_path)
        analyzer = DocumentAnalyzer()
        result = analyzer.analyze_document(text)
        log.info("Document analysis complete.")
        return result
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Error during document analysis")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")
