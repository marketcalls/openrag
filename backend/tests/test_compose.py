import json
import os
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path


def render_compose(*extra_args: str) -> dict:  # type: ignore[type-arg]
    compose = Path(__file__).parents[2] / "deploy" / "compose.yaml"
    docker = shutil.which("docker")
    assert docker is not None
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as secret_file:
        secret_value = secrets.token_urlsafe(32)
        secret_file.write(secret_value)
        secret_file.flush()
        environment = os.environ.copy()
        environment["OPENRAG_EVENT_REDIS_SECRET_FILE"] = secret_file.name
        rendered = subprocess.run(  # noqa: S603 - fixed executable and arguments
            [
                docker,
                "compose",
                "-f",
                str(compose),
                *extra_args,
                "config",
                "--format",
                "json",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
    assert secret_value not in rendered.stdout
    return json.loads(rendered.stdout)


def test_compose_contains_complete_application_stack() -> None:
    config = render_compose("--profile", "ml")
    required = {
        "postgres",
        "redis",
        "qdrant",
        "minio",
        "litellm",
        "migrate",
        "bootstrap",
        "api",
        "event-redis",
        "event-scheduler",
        "event-worker",
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


def test_backend_services_use_the_prebuilt_virtualenv_at_runtime() -> None:
    services = render_compose()["services"]

    assert services["migrate"]["command"][0] == "/app/.venv/bin/alembic"
    assert services["bootstrap"]["command"][0] == "/app/.venv/bin/python"
    assert services["api"]["command"][0] == "/app/.venv/bin/uvicorn"
    assert services["worker"]["command"][0] == "/app/.venv/bin/celery"
    assert services["event-worker"]["command"][0] == "/app/.venv/bin/celery"
    assert services["event-scheduler"]["command"][0] == "/app/.venv/bin/celery"
    assert services["api"]["healthcheck"]["test"][1] == "/app/.venv/bin/python"
    assert services["worker"]["mem_limit"] == "4294967296"
    assert services["worker"]["pids_limit"] == 256
    assert services["worker"]["security_opt"] == ["no-new-privileges:true"]


def test_event_transport_is_private_durable_and_failure_isolated() -> None:
    config = render_compose()
    services = config["services"]
    event_redis = services["event-redis"]

    assert event_redis["image"] == (
        "redis:7.4.9-alpine@sha256:"
        "6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99"
    )
    assert "ports" not in event_redis
    event_redis_command = " ".join(event_redis["command"])
    assert "--appendonly yes" in event_redis_command
    assert "--appendfsync always" in event_redis_command
    assert event_redis["security_opt"] == ["no-new-privileges:true"]
    assert event_redis["mem_limit"] == "536870912"
    assert config["networks"]["event-network"]["internal"] is True
    assert "eventredisdata" in config["volumes"]
    assert "event_redis_password" in config["secrets"]

    assert "event-redis" not in services["api"].get("depends_on", {})
    assert "event-redis" not in services["worker"].get("depends_on", {})
    api_secrets = {
        secret["source"] for secret in services["api"].get("secrets", [])
    }
    worker_secrets = {
        secret["source"]
        for secret in services["worker"].get("secrets", [])
    }
    assert "event_redis_password" not in api_secrets
    assert "event_redis_password" not in worker_secrets
    assert services["worker"]["command"][5] == "interactive,default"
    assert services["event-worker"]["command"][5] == "events"
    assert set(services["event-redis"]["networks"]) == {"event-network"}
    assert set(services["event-worker"]["networks"]) == {
        "default",
        "event-network",
    }
