from typing import List

from pydantic import BaseModel, ConfigDict, Field, field_validator
from urllib.parse import urlparse


class MediaFormatOption(BaseModel):
    format: str = Field(..., description='Output media format, such as mp4 or mp3')
    quality: str = Field(..., description='Target quality such as 720p or 1080p')
    extension: str | None = None
    resolution: str | None = None
    filesize: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None


class MediaAnalyzeRequest(BaseModel):
    url: str = Field(..., description='A valid HTTP or HTTPS video URL')

    @field_validator('url')
    @classmethod
    def validate_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError('URL cannot be empty.')

        parsed = urlparse(cleaned)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
            raise ValueError('URL must be a valid HTTP or HTTPS URL.')

        return cleaned


class MediaAnalyzeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(default=True)
    platform: str = Field(..., description='Detected platform name')
    extractor: str | None = None
    requires_authorization: bool = False
    extractor: str | None = None
    requires_authorization: bool = False
    title: str | None = Field(default=None, description='Provider-reported title, if available')
    duration: str | None = Field(default=None, description='Provider-reported duration, if available')
    thumbnail: str | None = Field(default=None, description='Provider-reported thumbnail, if available')
    webpage_url: str | None = None
    uploader: str | None = None
    available_formats: List[MediaFormatOption] = Field(
        default_factory=list,
        description='List of mock available media formats',
    )
