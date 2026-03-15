"""
POST /api/v1/ingest — Takes parsed content + ACL → chunks → classifies → embeds → stores.
"""

from fastapi import APIRouter, Request

from app.models.common import ErrorCode, ResponseEnvelope
from app.models.ingest import EntityCounts, IngestData, IngestRequest
from app.services.ingest.ingest_service import IngestError
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Ingestion Pipeline"])

_ERROR_CODE_MAP = {
    "VALIDATION_EMPTY_CONTENT": ErrorCode.VALIDATION_EMPTY_CONTENT,
    "VALIDATION_ACL_REQUIRED": ErrorCode.VALIDATION_ACL_REQUIRED,
    "EMBEDDING_MODEL_NOT_LOADED": ErrorCode.EMBEDDING_MODEL_NOT_LOADED,
    "EMBEDDING_FAILED": ErrorCode.EMBEDDING_FAILED,
    "EMBEDDING_OOM": ErrorCode.EMBEDDING_OOM,
    "QDRANT_CONNECTION_FAILED": ErrorCode.QDRANT_CONNECTION_FAILED,
    "QDRANT_COLLECTION_NOT_FOUND": ErrorCode.QDRANT_COLLECTION_NOT_FOUND,
    "QDRANT_UPSERT_FAILED": ErrorCode.QDRANT_UPSERT_FAILED,
    "QDRANT_DISK_FULL": ErrorCode.QDRANT_DISK_FULL,
    "CLASSIFY_FAILED": ErrorCode.CLASSIFY_FAILED,
}


@router.post(
    "/ingest",
    summary="Ingest a document into the RAG pipeline",
    description=(
        "Takes parsed text content with ACL permissions and processes it through the full ingestion pipeline:\n\n"
        "1. **Chunk** — Split content using `fixed`, `sentence`, or `late_chunking` (default) strategy\n"
        "2. **Classify** — Categorize into one of 9 municipality content types + extract entities\n"
        "3. **Embed** — Generate dense (1024-dim) + sparse vectors via BGE-M3\n"
        "4. **Store** — Upsert vectors into the specified Qdrant `collection_name` with flattened ACL payload\n\n"
        "**Multi-tenant:** The caller specifies which `collection_name` to store vectors in. "
        "The collection must be created first via `POST /api/v1/collections/init`.\n\n"
        "**Metadata:** Each vector point includes `organization_id` and `department` from the request metadata, "
        "enabling organizational filtering and attribution in search results.\n\n"
        "Previous vectors for the same `source_id` are deleted before upserting (idempotent).\n\n"
        "**Qdrant payload per vector:**\n"
        "```\n"
        "chunk_id, source_id, chunk_text, chunk_index, source_path,\n"
        "classification, language,\n"
        "acl_visibility, acl_allow_groups, acl_deny_groups, acl_allow_roles,\n"
        "acl_allow_users, acl_department,\n"
        "title, source_type, mime_type, uploaded_by,\n"
        "organization_id, department,\n"
        "entity_amounts, entity_deadlines\n"
        "```\n\n"
        "**Error codes:** `VALIDATION_EMPTY_CONTENT`, `VALIDATION_ACL_REQUIRED`, `EMBEDDING_MODEL_NOT_LOADED`, "
        "`EMBEDDING_FAILED`, `EMBEDDING_OOM`, `QDRANT_CONNECTION_FAILED`, `QDRANT_COLLECTION_NOT_FOUND`, "
        "`QDRANT_UPSERT_FAILED`, `QDRANT_DISK_FULL`, `CLASSIFY_FAILED`"
    ),
    response_description="Ingestion result with chunk count, vector count, classification, and timing",
)
async def ingest(body: IngestRequest, request: Request) -> ResponseEnvelope[IngestData]:
    request_id = request.state.request_id
    ingest_svc = request.app.state.ingest

    if not body.content.strip():
        return ResponseEnvelope(
            success=False,
            error=ErrorCode.VALIDATION_EMPTY_CONTENT,
            detail="Content must not be empty",
            request_id=request_id,
        )

    chunking = body.chunking
    acl_dict = body.acl.model_dump()
    metadata_dict = body.metadata.model_dump()

    try:
        result = await ingest_svc.ingest(
            source_id=body.source_id,
            file_path=body.file_path,
            content=body.content,
            acl=acl_dict,
            metadata=metadata_dict,
            collection_name=body.collection_name,
            language=body.language,
            chunking_strategy=chunking.strategy if chunking else "late_chunking",
            max_chunk_size=chunking.max_chunk_size if chunking else None,
            chunk_overlap=chunking.overlap if chunking else None,
        )
    except IngestError as e:
        error_code = _ERROR_CODE_MAP.get(e.code, ErrorCode.EMBEDDING_FAILED)
        log.error("ingest_failed", source_id=body.source_id, error=str(e), code=e.code)
        return ResponseEnvelope(
            success=False,
            error=error_code,
            detail=str(e),
            request_id=request_id,
        )

    return ResponseEnvelope(
        success=True,
        data=IngestData(
            source_id=result.source_id,
            chunks_created=result.chunks_created,
            vectors_stored=result.vectors_stored,
            collection=result.collection,
            classification=result.classification,
            entities_extracted=EntityCounts(
                dates=result.entities_extracted.get("dates", 0),
                contacts=result.entities_extracted.get("contacts", 0),
                amounts=result.entities_extracted.get("amounts", 0),
            ),
            embedding_time_ms=result.embedding_time_ms,
            total_time_ms=result.total_time_ms,
        ),
        request_id=request_id,
    )
