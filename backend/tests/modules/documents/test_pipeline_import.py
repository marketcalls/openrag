import subprocess
import sys


def test_importing_pipeline_does_not_eagerly_import_docling() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import openrag.modules.documents.pipeline; "
                "raise SystemExit('docling' in sys.modules)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
