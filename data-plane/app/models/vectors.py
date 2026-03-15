from pydantic import BaseModel, Field

from app.models.common import ACL


class UpdateACLRequest(BaseModel):
    """Request to update ACL/permissions on existing vectors without re-embedding.

    Updates the payload on all Qdrant points matching the source_id.
    """

    collection_name: str = Field(..., description="Qdrant collection name")
    source_id: str = Field(..., description="Document ID whose vectors to update")
    acl: ACL = Field(..., description="New ACL to apply to all vectors for this document")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "collection_name": "wiener-neudorf",
                    "source_id": "doc_abc123",
                    "acl": {
                        "allow_groups": ["DOMAIN\\Bauamt-Mitarbeiter", "DOMAIN\\Neue-Gruppe"],
                        "deny_groups": [],
                        "allow_roles": [],
                        "allow_users": [],
                        "visibility": "internal",
                    },
                }
            ]
        }
    }


class DeleteVectorsData(BaseModel):
    """Result of deleting all vectors for a document."""

    source_id: str = Field(..., description="Document ID whose vectors were deleted")
    vectors_deleted: int = Field(..., description="Number of vectors removed from Qdrant")


class UpdateACLData(BaseModel):
    """Result of updating ACL on existing vectors."""

    source_id: str = Field(..., description="Document ID whose vectors were updated")
    vectors_updated: int = Field(..., description="Number of vectors with updated ACL payload")
