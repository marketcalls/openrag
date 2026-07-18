from pathlib import Path


def test_backend_dockerfile_supports_runtime_processes() -> None:
    text = (Path(__file__).parents[2] / "backend" / "Dockerfile").read_text()
    assert "uv sync --frozen --no-dev" in text
    assert "COPY src ./src" in text
    assert "COPY migrations ./migrations" in text
    assert "USER openrag" in text


def test_backend_container_runs_the_prebuilt_virtualenv_without_syncing() -> None:
    text = (Path(__file__).parents[2] / "backend" / "Dockerfile").read_text()
    assert 'CMD ["/app/.venv/bin/uvicorn"' in text
    assert 'CMD ["uv", "run"' not in text


def test_backend_runtime_uses_cpu_only_pytorch() -> None:
    backend = Path(__file__).parents[2] / "backend"
    text = (backend / "pyproject.toml").read_text()
    assert '"torch>=2.2"' in text
    assert '"torchvision>=0.17"' in text
    assert 'name = "pytorch-cpu"' in text
    assert 'url = "https://download.pytorch.org/whl/cpu"' in text
    assert 'torch = { index = "pytorch-cpu" }' in text
    assert 'torchvision = { index = "pytorch-cpu" }' in text
    lock = (backend / "uv.lock").read_text()
    assert "download.pytorch.org/whl/cpu" in lock
    assert 'name = "nvidia-cudnn-cu13"' not in lock
