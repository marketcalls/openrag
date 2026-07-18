import json
import shutil
import subprocess
from pathlib import Path


def test_compose_contains_complete_application_stack() -> None:
    compose = Path(__file__).parents[2] / "deploy" / "compose.yaml"
    docker = shutil.which("docker")
    assert docker is not None
    rendered = subprocess.run(  # noqa: S603 - fixed executable and arguments
        [
            docker,
            "compose",
            "-f",
            str(compose),
            "--profile",
            "ml",
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    config = json.loads(rendered.stdout)
    required = {
        "postgres",
        "redis",
        "qdrant",
        "minio",
        "litellm",
        "migrate",
        "bootstrap",
        "api",
        "worker",
        "web",
    }
    assert required <= set(config["services"])
    assert (
        config["services"]["api"]["depends_on"]["bootstrap"]["condition"]
        == "service_completed_successfully"
    )
    assert "kekdata" in config["volumes"]
    assert "ollama" in config["services"]
