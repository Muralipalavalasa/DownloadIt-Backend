from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.media import MediaAnalyzeRequest


class DownloadCreateRequest(BaseModel):
    source_url: str = Field(..., max_length=2048)
    format: str = Field(default='mp4', min_length=1, max_length=20)
    quality: str = Field(default='source', min_length=1, max_length=40)
    authorized_to_download: bool = False

    @field_validator('source_url')
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        return MediaAnalyzeRequest(url=value).url


class DownloadCreatedResponse(BaseModel):
    id: UUID
    status: Literal['pending']


class DownloadHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_url: str
    platform: str
    title: str | None
    status: str
    format: str
    quality: str
    created_at: datetime
    completed_at: datetime | None


class DownloadDetail(DownloadHistoryItem):
    user_id: UUID
    expires_at: datetime | None
    error_message: str | None


class DownloadStatusResponse(BaseModel):
    id: UUID
    status: Literal['pending', 'processing', 'completed', 'failed', 'expired']
    progress: int | None = None
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    error: str | None = None


class DeleteDownloadResponse(BaseModel):
    success: bool