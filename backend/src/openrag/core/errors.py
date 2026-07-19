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


class WorkspaceAccessDenied(AuthorizationError):
    title = "Workspace access denied"


class NotFoundError(OpenRAGError):
    status_code = 404
    title = "Not found"


class ConflictError(OpenRAGError):
    status_code = 409
    title = "Conflict"


class RateLimitExceeded(OpenRAGError):
    status_code = 429
    title = "Too many requests"


class PayloadTooLarge(OpenRAGError):
    status_code = 413
    title = "Payload too large"


class UnsupportedMediaType(OpenRAGError):
    status_code = 415
    title = "Unsupported media type"


class SecretsError(OpenRAGError):
    status_code = 500
    title = "Secrets subsystem error"


class UpstreamError(OpenRAGError):
    status_code = 502
    title = "Upstream service error"
