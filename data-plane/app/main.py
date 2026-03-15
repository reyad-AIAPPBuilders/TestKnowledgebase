import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.middleware.hmac_auth import HMACAuthMiddleware
from app.middleware.request_id import RequestIDMiddleware
from app.routers import (
    classify,
    collections,
    discover,
    health,
    ingest,
    metrics,
    parse,
    scrape,
    search,
    vectors,
)
from app.services.discovery.discovery_service import DiscoveryService
from app.services.discovery.r2_client import R2Client
from app.services.discovery.smb_client import SMBClient
from app.services.embedding.bge_m3_client import BGEM3Client
from app.services.embedding.qdrant_service import QdrantService
from app.services.ingest.ingest_service import IngestService
from app.services.intelligence.chunker import Chunker
from app.services.intelligence.classifier import Classifier
from app.services.parsing.parser_service import ParserService
from app.services.scraping.scraper_service import ScraperService
from app.services.scraping.sitemap import SitemapParser
from app.services.search.search_service import SearchService
from app.utils.logger import get_logger, setup_logging

setup_logging()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    app.state.start_time = time.monotonic()

    # Allow tests to inject fake services before TestClient startup
    if getattr(app.state, "_test_mode", False):
        log.info("app_started_test_mode")
        yield
        log.info("app_stopped_test_mode")
        return

    # ── Scraping ─────────────────────────────────────
    scraping_svc = ScraperService()
    await scraping_svc.startup()
    app.state.scraping = scraping_svc

    sitemap_parser = SitemapParser()
    app.state.sitemap_parser = sitemap_parser

    # ── Parsing ──────────────────────────────────────
    parser_svc = ParserService()
    await parser_svc.startup()
    app.state.parser = parser_svc

    # ── Intelligence ─────────────────────────────────
    classifier = Classifier()
    app.state.classifier = classifier

    # ── Embedding + Storage ──────────────────────────
    embedder = BGEM3Client()
    await embedder.startup()
    app.state.embedder = embedder

    qdrant = QdrantService()
    await qdrant.startup()
    app.state.qdrant = qdrant

    # ── Discovery ────────────────────────────────────
    smb_client = SMBClient()
    r2_client = R2Client()
    await r2_client.startup()
    app.state.discovery = DiscoveryService(smb_client, r2_client)
    app.state.r2_client = r2_client

    # ── Ingest + Search ──────────────────────────────
    chunker = Chunker()
    app.state.ingest = IngestService(chunker, classifier, embedder, qdrant)
    app.state.search = SearchService(embedder, qdrant)

    log.info("app_started", mode=settings.mode, version=settings.version)
    yield

    # ── Shutdown ─────────────────────────────────────
    await scraping_svc.shutdown()
    await parser_svc.shutdown()
    await embedder.shutdown()
    await qdrant.shutdown()
    await r2_client.shutdown()
    await sitemap_parser.close()

    log.info("app_stopped")


tags_metadata = [
    {
        "name": "Health",
        "description": "Liveness and readiness probes for container orchestrators and load balancers. "
        "The `/ready` endpoint checks connectivity to Qdrant, BGE-M3, Parser (LlamaParse or Unstructured), Crawl4AI, LDAP, and Redis.",
    },
    {
        "name": "Metrics",
        "description": "Prometheus-compatible metrics endpoint (`dp_` prefix).",
    },
    {
        "name": "File Discovery",
        "description": "Scan SMB file shares or Cloudflare R2 buckets for new/changed documents. "
        "Returns file metadata, SHA-256 hashes, and NTFS ACLs for change detection.",
    },
    {
        "name": "Web Scraping",
        "description": "Scrape webpages via Crawl4AI and discover URLs from sitemaps or BFS crawling.",
    },
    {
        "name": "Document Parsing",
        "description": "Extract text, tables, and metadata from documents.\n\n"
        "**Supported formats:** PDF, DOCX, DOC, PPTX, ODT, XLSX, XLS, TXT, CSV, HTML, RTF.\n\n"
        "**Parser backends** (auto-selected at startup):\n"
        "- **LlamaParse** (cloud) — `LLAMA_CLOUD_API_KEY` set → uploads to LlamaCloud API for high-quality markdown extraction\n"
        "- **Unstructured** (local) — no API key → runs entirely locally, ideal for on-premise deployments\n"
        "- **SpreadsheetParser** — always used for XLSX/XLS (openpyxl)\n"
        "- **TextParser** — always used for TXT, CSV, HTML, RTF",
    },
    {
        "name": "Content Intelligence",
        "description": "Classify municipality content into 9 categories (funding, event, policy, contact, form, announcement, minutes, report, general) "
        "and extract structured entities (dates, deadlines, monetary amounts, email contacts, departments).",
    },
    {
        "name": "Ingestion Pipeline",
        "description": "Full RAG ingestion pipeline: chunk → classify → embed (BGE-M3) → store (Qdrant).\n\n"
        "**Key features:**\n"
        "- Caller specifies the target `collection_name` (multi-tenant)\n"
        "- ACL-aware payloads with visibility-based permission filtering\n"
        "- Metadata includes `organization_id` and `department` for organizational context\n"
        "- Idempotent: re-ingesting the same `source_id` replaces old vectors automatically\n"
        "- Chunking strategies: `late_chunking` (paragraph-aware, default), `sentence`, or `fixed`",
    },
    {
        "name": "Semantic Search",
        "description": "Permission-aware semantic search across Qdrant collections.\n\n"
        "**Key features:**\n"
        "- Caller specifies the target `collection_name` to search in\n"
        "- Mandatory user context for ACL filtering (citizen → public only; employee → public + internal with AD group intersection)\n"
        "- Results include `organization_id`, `department`, entity data, and classification\n"
        "- Optional filtering by content category (e.g. `funding`, `policy`)",
    },
    {
        "name": "Vector Management",
        "description": "Delete vectors or update ACL permissions on existing vector points.\n\n"
        "- `DELETE /vectors/{source_id}?collection_name=...` — remove all vectors for a document\n"
        "- `PUT /vectors/update-acl` — update ACL payload on vectors without re-embedding",
    },
    {
        "name": "Collection Management",
        "description": "Create and inspect Qdrant vector collections for municipality tenants. "
        "Each collection stores dense (1024-dim BGE-M3) and optional sparse vectors for hybrid search.",
    },
]

app = FastAPI(
    title="KI² Data Plane",
    description=(
        "Unified ingestion, embedding, and permission-aware search for municipality RAG pipelines.\n\n"
        "## Authentication\n"
        "All endpoints (except `/health`) require HMAC-SHA256 authentication via:\n"
        "- `X-Signature`: HMAC-SHA256 of `timestamp.body`\n"
        "- `X-Timestamp`: Unix epoch seconds (must be within ±5 min)\n\n"
        "## Pipeline Flow\n"
        "1. **Discover** → Scan file sources (SMB shares, Cloudflare R2) for new/changed documents\n"
        "2. **Scrape / Parse** → Extract text content from web pages (Crawl4AI) or files (LlamaParse / Unstructured)\n"
        "3. **Ingest** → Chunk, classify, embed (BGE-M3), and store in Qdrant with ACL + metadata\n"
        "4. **Search** → Permission-filtered semantic search across collections\n\n"
        "## Document Parsing\n"
        "Two parsing backends are available, selected automatically at startup:\n"
        "- **LlamaParse** (cloud) — when `LLAMA_CLOUD_API_KEY` is set. High-quality parsing for PDF, DOCX, DOC, PPTX, ODT via the LlamaCloud API.\n"
        "- **Unstructured** (local) — when no API key is set. Runs entirely locally for on-premise deployments.\n\n"
        "## Multi-Tenant Collections\n"
        "Every **ingest**, **search**, **delete**, and **update-acl** request requires a `collection_name` field.\n"
        "Collections are created via `POST /api/v1/collections/init` and represent a municipality/tenant.\n\n"
        "## Vector Payload Metadata\n"
        "Each vector point stored in Qdrant carries the following metadata:\n"
        "- **ACL fields**: `acl_visibility`, `acl_allow_groups`, `acl_deny_groups`, `acl_allow_roles`, `acl_allow_users`, `acl_department`\n"
        "- **Document metadata**: `title`, `source_type`, `mime_type`, `uploaded_by`, `organization_id`, `department`\n"
        "- **Intelligence**: `classification`, `language`, `entity_amounts`, `entity_deadlines`\n"
    ),
    version=settings.version,
    lifespan=lifespan,
    openapi_tags=tags_metadata,
)

# Middleware (applied in reverse order — last added runs first)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(HMACAuthMiddleware)
app.add_middleware(RequestIDMiddleware)

# Routers
app.include_router(health.router)
app.include_router(metrics.router)
app.include_router(scrape.router)
app.include_router(parse.router)
app.include_router(classify.router)
app.include_router(vectors.router)
app.include_router(collections.router)
app.include_router(discover.router)
app.include_router(ingest.router)
app.include_router(search.router)
