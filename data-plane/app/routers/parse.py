"""
POST /api/v1/parse — Parse a document from URL, SMB, or R2 source.
POST /api/v1/parse/upload — Parse an uploaded document file.
"""

import os
import tempfile

from fastapi import APIRouter, File, Request, UploadFile

from app.models.common import ErrorCode, ResponseEnvelope
from app.models.parse import ParseData, ParseRequest
from app.services.parsing.models import ParseStatus
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Document Parsing"])


@router.post(
    "/parse",
    summary="Parse a document",
    description="Extract text, tables, and metadata from a document.\n\n"
    "**Sources:**\n"
    "- **url**: Any public URL pointing to a document (PDF, DOCX, etc.) — the file is downloaded and parsed\n"
    "- **smb**: Reads from a mounted file share path\n"
    "- **r2**: Downloads via pre-signed URL from Cloudflare R2, then parses\n\n"
    "**Supported formats:** PDF, DOCX, DOC, PPTX, ODT, XLSX, XLS, TXT, CSV, HTML, RTF.\n\n"
    "**Parser backends** (auto-selected at startup):\n"
    "- **LlamaParse** (cloud) — `LLAMA_CLOUD_API_KEY` set → high-quality markdown extraction\n"
    "- **Lightweight local** — no API key → PyMuPDF for PDF, python-docx for DOCX (no heavy dependencies)\n"
    "- **SpreadsheetParser** — always used for XLSX/XLS\n"
    "- **TextParser** — always used for TXT, CSV, HTML, RTF\n\n"
    "**Error codes:** `PARSE_FAILED`, `PARSE_ENCRYPTED`, `PARSE_CORRUPTED`, `PARSE_EMPTY`, "
    "`PARSE_TIMEOUT`, `PARSE_UNSUPPORTED_FORMAT`, `R2_FILE_NOT_FOUND`",
    response_description="Extracted text content with page count, language, and table count",
)
async def parse(body: ParseRequest, request: Request) -> ResponseEnvelope[ParseData]:
    request_id = request.state.request_id
    parser = request.app.state.parser

    if body.source == "url":
        # Download from public URL and parse
        result = await parser.parse_from_url(
            url=body.file_path,
            mime_type=body.mime_type,
        )

    elif body.source == "r2":
        if not body.r2_presigned_url:
            return ResponseEnvelope(
                success=False,
                error=ErrorCode.R2_FILE_NOT_FOUND,
                detail="r2_presigned_url is required when source is r2",
                request_id=request_id,
            )
        result = await parser.parse_from_url(
            url=body.r2_presigned_url,
            mime_type=body.mime_type,
        )

    else:
        # SMB source — file_path is a local/mounted path
        result = await parser.parse_from_file(
            file_path=body.file_path,
            mime_type=body.mime_type,
            filename=body.file_path.rsplit("/", 1)[-1] if "/" in body.file_path else body.file_path,
        )

    return _build_response(result, body.file_path, request_id)


@router.post(
    "/parse/upload",
    summary="Parse an uploaded document",
    description="Upload a document file directly and extract text, tables, and metadata.\n\n"
    "Supports the same formats as `/parse`: PDF, DOCX, DOC, PPTX, ODT, XLSX, XLS, TXT, CSV, HTML, RTF.",
    response_description="Extracted text content with page count, language, and table count",
)
async def parse_upload(request: Request, file: UploadFile = File(...)) -> ResponseEnvelope[ParseData]:
    request_id = request.state.request_id
    parser = request.app.state.parser

    # Save upload to temp file
    suffix = os.path.splitext(file.filename or "")[1] or ""
    fd, temp_path = tempfile.mkstemp(suffix=suffix)
    try:
        content = await file.read()
        with os.fdopen(fd, "wb") as f:
            f.write(content)

        result = await parser.parse_from_file(
            file_path=temp_path,
            mime_type=file.content_type,
            filename=file.filename,
        )

        return _build_response(result, file.filename or "upload", request_id)

    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _build_response(result, file_path: str, request_id: str) -> ResponseEnvelope[ParseData]:
    """Convert a ParseResult into the standard API response."""
    if result.status == ParseStatus.UNSUPPORTED:
        return ResponseEnvelope(
            success=False,
            error=ErrorCode.PARSE_UNSUPPORTED_FORMAT,
            detail=result.error,
            request_id=request_id,
        )

    if result.status == ParseStatus.FAILED:
        error_code = _map_parse_error(result.error)
        return ResponseEnvelope(
            success=False,
            error=error_code,
            detail=result.error,
            request_id=request_id,
        )

    content = result.text or ""
    if not content.strip():
        return ResponseEnvelope(
            success=False,
            error=ErrorCode.PARSE_EMPTY,
            detail="Document contained no extractable text",
            request_id=request_id,
        )

    return ResponseEnvelope(
        success=True,
        data=ParseData(
            file_path=file_path,
            content=content,
            pages=result.pages_parsed,
            language=result.metadata.language,
            extracted_tables=len(result.tables),
            content_length=len(content),
        ),
        request_id=request_id,
    )


def _map_parse_error(error_msg: str | None) -> str:
    error_lower = (error_msg or "").lower()
    if "encrypt" in error_lower or "password" in error_lower:
        return ErrorCode.PARSE_ENCRYPTED
    if "corrupt" in error_lower or "damaged" in error_lower:
        return ErrorCode.PARSE_CORRUPTED
    if "timeout" in error_lower:
        return ErrorCode.PARSE_TIMEOUT
    return ErrorCode.PARSE_FAILED
