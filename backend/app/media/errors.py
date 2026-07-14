from __future__ import annotations


class MediaError(Exception):
    """A stable, non-sensitive media processing error."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class MediaTooLargeError(MediaError):
    def __init__(self, limit: int) -> None:
        super().__init__("media_too_large", f"Media exceeds the {limit} byte limit")


class UnsafeMediaUrlError(MediaError):
    def __init__(self, message: str = "Media URL points to a forbidden destination") -> None:
        super().__init__("unsafe_media_url", message)
