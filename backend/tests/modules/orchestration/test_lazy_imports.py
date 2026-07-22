import subprocess
import sys


def test_api_factory_does_not_eagerly_import_litellm() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "from openrag.api.app import create_app; "
                "create_app(); "
                "raise SystemExit('litellm' in sys.modules)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=40,
    )

    assert result.returncode == 0, result.stderr


def test_celery_task_registry_does_not_eagerly_import_provider_or_document_stacks() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import openrag.worker.tasks; "
                "blocked = ('docling', 'fastembed', 'litellm', 'onnxruntime', 'qdrant_client'); "
                "loaded = sorted(name for name in blocked if name in sys.modules); "
                "print(','.join(loaded)); "
                "raise SystemExit(bool(loaded))"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=40,
    )

    assert result.returncode == 0, result.stdout or result.stderr
