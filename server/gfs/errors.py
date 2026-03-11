from __future__ import annotations


class GfsError(Exception):
    status_code = 500
    retryable = False

    def __init__(self, message: str, provider: str = "gfs") -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider

    def to_json(self) -> dict[str, object]:
        return {
            "error": self.__class__.__name__.replace("Error", "").lower(),
            "message": self.message,
            "provider": self.provider,
            "retryable": self.retryable,
        }


class InvalidBBoxError(GfsError):
    status_code = 400


class NotFoundError(GfsError):
    status_code = 404


class ProviderUnavailableError(GfsError):
    status_code = 503
    retryable = True


class UpstreamTimeoutError(GfsError):
    status_code = 504
    retryable = True
