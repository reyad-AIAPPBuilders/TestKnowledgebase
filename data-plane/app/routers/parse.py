"""
POST /api/v1/parse — Parse a document from SMB or R2 source.
"""

from fastapi import APIRouter, Request

from app.models.common import ErrorCode, ResponseEnvelope
from app.models.parse import ParseData, ParseRequest
from app.services.parsing.models import ParseStatus
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Document Parsing"])


@router.post(
    "/parse",
    summary="Parse a document",
    description="Extract text, tables, and metadata from a document stored on SMB file shares or Cloudflare R2.\n\nSupported formats: **PDF**, **DOCX**, **DOC**, **PPTX**, **ODT** (via LlamaParse cloud or Unstructured local), **XLSX/XLS** (openpyxl), **TXT**, **CSV**, **HTML**, **RTF**.\n\n- **Cloud mode** (`LLAMA_CLOUD_API_KEY` set): Uses LlamaParse for high-quality document parsing\n- **Local mode** (no API key): Uses Unstructured library for local parsing\n\n- **SMB source**: Reads from a mounted file share path\n- **R2 source**: Downloads via pre-signed URL, then parses locally\n\n**Error codes:** `PARSE_FAILED`, `PARSE_ENCRYPTED`, `PARSE_CORRUPTED`, `PARSE_EMPTY`, `PARSE_TIMEOUT`, `PARSE_UNSUPPORTED_FORMAT`, `R2_FILE_NOT_FOUND`",
    response_description="Extracted text content with page count, language, and table count",
)
async def parse(body: ParseRequest, request: Request) -> ResponseEnvelope[ParseData]:
    request_id = request.state.request_id
    parser = request.app.state.parser

    # For R2 sources, download via pre-signed URL then parse the local file
    if body.source == "r2":
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
            file_path=body.file_path,
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
