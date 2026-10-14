from fastapi import APIRouter

from app.providers.errors import MediaProcessingError, ProviderError, to_http_exception
from app.schemas.media import MediaAnalyzeRequest, MediaAnalyzeResponse
from app.services.media_service import analyze_url

router = APIRouter()


@router.post('/media/analyze', response_model=MediaAnalyzeResponse)
def analyze_media(request: MediaAnalyzeRequest) -> MediaAnalyzeResponse:
    try:
        result = analyze_url(request.url)
        return MediaAnalyzeResponse(**result)
    except ProviderError as exc:
        raise to_http_exception(exc) from exc
    except Exception as exc:  # pragma: no cover - safeguards against unexpected failures
        error = MediaProcessingError('Media provider processing failed.')
        raise to_http_exception(error) from exc
