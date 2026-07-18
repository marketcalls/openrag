from openrag.core.errors import AuthenticationError, NotFoundError, OpenRAGError


def test_error_hierarchy() -> None:
    error = NotFoundError("document missing")
    assert isinstance(error, OpenRAGError)
    assert error.status_code == 404
    assert error.detail == "document missing"
    assert AuthenticationError("").status_code == 401
