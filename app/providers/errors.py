from fastapi import HTTPException


class ProviderError(Exception):
    pass


class UnsupportedPlatform(ProviderError):
    def __init__(self, platform: str = 'unknown') -> None:
        self.platform = platform
        super().__init__('Platform is not currently supported.')


class InvalidMediaUrl(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class MediaNotDownloadable(ProviderError):
    pass


class FFmpegUnavailable(ProviderError):
    pass


class RequestedQualityUnavailable(ProviderError):
    pass


class AuthorizationRequired(ProviderError):
    pass


class MediaProcessingError(ProviderError):
    pass


def to_http_exception(error: ProviderError) -> HTTPException:
    if isinstance(error, ProviderUnavailable):
        status_code = 503
        public_message = 'Media provider is temporarily unavailable.'
    elif isinstance(error, FFmpegUnavailable):
        status_code = 503
        public_message = 'FFmpeg is required for this video/audio operation.'
    elif isinstance(error, RequestedQualityUnavailable):
        status_code = 400
        public_message = 'Requested quality is not available for this video.'
    elif isinstance(error, AuthorizationRequired):
        status_code = 400
        public_message = 'Confirm you are authorized to download this media and that the platform permits it.'
    elif isinstance(error, MediaProcessingError):
        status_code = 502
        public_message = 'Media provider processing failed.'
    elif isinstance(error, InvalidMediaUrl):
        status_code = 422
        public_message = 'Enter a valid media URL.'
    elif isinstance(error, UnsupportedPlatform):
        status_code = 400
        public_message = 'Platform is not currently supported.'
    else:
        status_code = 400
        public_message = 'Media cannot be downloaded from this provider.'

    platform = error.platform if isinstance(error, UnsupportedPlatform) else None
    detail: dict[str, str | bool] = {
        'success': False,
        'message': public_message,
    }
    if platform is not None:
        detail['platform'] = platform
    return HTTPException(status_code=status_code, detail=detail)