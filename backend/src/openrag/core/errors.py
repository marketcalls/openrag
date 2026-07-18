class OpenRAGError(Exception):
    """Base for typed application errors mapped to problem responses."""

    status_code: int = 500
    title: str = "Internal error"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail)


class AuthenticationError(OpenRAGError):
    status_code = 401
    title = "Authentication failed"


class AuthorizationError(OpenRAGError):
    status_code = 403
    title = "Not permitted"


class NotFoundError(OpenRAGError):
    status_code = 404
    title = "Not found"


class ConflictError(OpenRAGError):
    status_code = 409
    title = "Conflict"


class RateLimitExceeded(OpenRAGError):
    status_code = 429
    title = "Too many requests"
