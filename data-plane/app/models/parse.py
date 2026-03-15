from pydantic import BaseModel, Field


class ParseRequest(BaseModel):
    """Request to parse a document from a URL, SMB file share, or Cloudflare R2 storage."""

    file_path: str = Field(..., description="Document URL, SMB path (e.g. //server/share/doc.pdf), or R2 object key")
    source: str = Field(..., description="Storage source: 'url' (public URL), 'smb' (mounted file share), or 'r2' (Cloudflare R2)", pattern=r"^(url|smb|r2)$")
    r2_presigned_url: str | None = Field(None, description="Pre-signed download URL (required when source is 'r2')")
    mime_type: str | None = Field(None, description="MIME type of the file (e.g. application/pdf). Auto-detected for URL source.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "file_path": "https://example.com/report.pdf",
                    "source": "url",
                },
                {
                    "file_path": "//server/bauamt/dokumente/antrag_001.pdf",
                    "source": "smb",
                    "mime_type": "application/pdf",
                },
                {
                    "file_path": "tenant/wiener-neudorf/uploads/report.docx",
                    "source": "r2",
                    "r2_presigned_url": "https://r2.example.com/presigned/report.docx?token=abc123",
                    "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                },
            ]
        }
    }


class ParseData(BaseModel):
    """Extracted content from a parsed document."""

    file_path: str = Field(..., description="Original file path from the request")
    content: str = Field(..., description="Extracted text content from the document")
    pages: int | None = Field(None, description="Number of pages successfully parsed")
    language: str | None = Field(None, description="Detected document language (ISO 639-1)")
    extracted_tables: int = Field(0, description="Number of tables extracted from the document")
    content_length: int = Field(..., description="Length of extracted content in characters")
