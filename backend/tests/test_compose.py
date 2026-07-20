import json
import os
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path


def render_compose(
    *extra_args: str,
    environment_overrides: dict[str, str] | None = None,
) -> dict:  # type: ignore[type-arg]
    compose = Path(__file__).parents[2] / "deploy" / "compose.yaml"
    docker = shutil.which("docker")
    assert docker is not None
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as secret_file:
        secret_value = secrets.token_urlsafe(32)
        secret_file.write(secret_value)
        secret_file.flush()
        environment = os.environ.copy()
        environment["OPENRAG_EVENT_REDIS_SECRET_FILE"] = secret_file.name
        environment.update(environment_overrides or {})
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
        "authority-provisioner",
        "bootstrap",
        "api",
        "event-redis",
        "event-scheduler",
        "event-worker",
        "evaluation-worker",
        "run-worker",
        "summary-worker",
        "worker",
        "web",
    }
    assert required <= set(config["services"])
    assert (
        config["services"]["api"]["depends_on"]["bootstrap"]["condition"]
        == "service_completed_successfully"
    )
    assert (
        config["services"]["api"]["depends_on"]["authority-provisioner"]["condition"]
        == "service_completed_successfully"
    )
    assert "kekdata" in config["volumes"]
    assert "ollama" in config["services"]


def test_backend_services_use_the_prebuilt_virtualenv_at_runtime() -> None:
    services = render_compose()["services"]

    assert services["migrate"]["command"][0] == "/app/.venv/bin/alembic"
    assert services["bootstrap"]["command"][0] == "/app/.venv/bin/python"
    assert services["authority-provisioner"]["command"][:4] == [
        "/app/.venv/bin/python",
        "-m",
        "openrag.cli",
        "authority",
    ]
    assert services["api"]["command"][0] == "/app/.venv/bin/uvicorn"
    assert services["worker"]["command"][0] == "/app/.venv/bin/celery"
    assert services["event-worker"]["command"][0] == "/app/.venv/bin/celery"
    assert services["run-worker"]["command"][0] == "/app/.venv/bin/celery"
    assert services["summary-worker"]["command"][0] == "/app/.venv/bin/celery"
    assert services["evaluation-worker"]["command"][0] == "/app/.venv/bin/celery"
    assert services["event-scheduler"]["command"][0] == "/app/.venv/bin/celery"
    assert services["api"]["healthcheck"]["test"][1] == "/app/.venv/bin/python"
    assert services["worker"]["mem_limit"] == "4294967296"
    assert services["worker"]["pids_limit"] == 256
    assert services["worker"]["security_opt"] == ["no-new-privileges:true"]


def test_database_pool_budget_is_explicit_and_below_postgres_capacity() -> None:
    config = render_compose()
    services = config["services"]
    environment = services["api"]["environment"]
    pool_size = int(environment["OPENRAG_DATABASE_POOL_SIZE"])
    max_overflow = int(environment["OPENRAG_DATABASE_MAX_OVERFLOW"])
    process_count = int(environment["OPENRAG_DATABASE_PROCESS_COUNT"])
    budget = int(environment["OPENRAG_DATABASE_CONNECTION_BUDGET"])
    postgres_command = " ".join(services["postgres"]["command"])

    assert (pool_size + max_overflow) * process_count <= budget
    assert budget == 160
    assert "max_connections=200" in postgres_command


def test_authority_generation_is_provisioned_before_writes() -> None:
    services = render_compose()["services"]
    generation = services["api"]["environment"]["OPENRAG_AUTHORITY_GENERATION_ID"]

    assert (
        generation == services["ingestion-worker"]["environment"]["OPENRAG_AUTHORITY_GENERATION_ID"]
    )
    assert (
        services["authority-provisioner"]["environment"]["OPENRAG_AUTHORITY_GENERATION_ID"]
        == generation
    )
    assert services["authority-provisioner"]["restart"] == "no"
    assert services["authority-provisioner"]["depends_on"]["qdrant"]["condition"] == (
        "service_healthy"
    )
    assert (
        services["ingestion-worker"]["depends_on"]["authority-provisioner"]["condition"]
        == "service_completed_successfully"
    )


def test_event_transport_is_private_durable_and_failure_isolated() -> None:
    config = render_compose()
    services = config["services"]
    event_redis = services["event-redis"]

    assert event_redis["image"] == (
        "redis:7.4.9-alpine@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99"
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

    assert services["api"]["depends_on"]["event-redis"]["condition"] == ("service_healthy")
    assert "event-redis" not in services["worker"].get("depends_on", {})
    api_secrets = {secret["source"] for secret in services["api"].get("secrets", [])}
    worker_secrets = {secret["source"] for secret in services["worker"].get("secrets", [])}
    assert "event_redis_password" in api_secrets
    assert "event_redis_password" not in worker_secrets
    assert services["worker"]["command"][5] == "interactive,default"
    assert services["event-worker"]["command"][5] == "events"
    assert services["run-worker"]["command"][5] == "runs"
    assert services["summary-worker"]["command"][5] == "summaries"
    assert services["evaluation-worker"]["command"][5] == "evaluations"
    assert set(services["event-redis"]["networks"]) == {"event-network"}
    assert set(services["event-worker"]["networks"]) == {
        "default",
        "event-network",
        "observability-network",
    }
    assert set(services["api"]["networks"]) == {
        "default",
        "event-network",
        "observability-network",
    }
    assert set(services["run-worker"]["networks"]) == {
        "default",
        "event-network",
        "observability-network",
    }
    assert {secret["source"] for secret in services["run-worker"]["secrets"]} == {
        "event_redis_password"
    }
    assert "secrets" not in services["summary-worker"]
    assert "secrets" not in services["evaluation-worker"]
    assert services["evaluation-worker"]["security_opt"] == ["no-new-privileges:true"]


def test_observability_stores_are_private_and_grafana_is_loopback_only() -> None:
    config = render_compose("--profile", "observability")
    services = config["services"]

    for name in ("otel-collector", "prometheus", "loki", "tempo"):
        assert "ports" not in services[name]
        assert services[name]["security_opt"] == ["no-new-privileges:true"]

    assert services["grafana"]["ports"] == [
        {
            "mode": "ingress",
            "target": 3000,
            "published": "53000",
            "protocol": "tcp",
            "host_ip": "127.0.0.1",
        }
    ]
    assert services["grafana"]["environment"]["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    assert services["grafana"]["environment"]["GF_SECURITY_ADMIN_PASSWORD__FILE"] == (
        "/run/secrets/grafana_admin_password"  # noqa: S105 - secret file path
    )
    assert config["networks"]["observability-network"]["internal"] is True
    assert set(services["otel-collector"]["networks"]) == {"observability-network"}
    assert set(services["prometheus"]["networks"]) == {"observability-network"}
    assert set(services["loki"]["networks"]) == {"observability-network"}
    assert set(services["tempo"]["networks"]) == {"observability-network"}
    assert set(services["grafana"]["networks"]) == {"observability-network"}


def test_openrag_runtimes_export_only_over_the_private_observability_network() -> None:
    services = render_compose(
        "--profile",
        "observability",
        environment_overrides={"OPENRAG_OTEL_ENDPOINT": "http://otel-collector:4317"},
    )["services"]
    exporters = (
        "api",
        "worker",
        "ingestion-worker",
        "event-worker",
        "run-worker",
        "summary-worker",
        "evaluation-worker",
        "event-scheduler",
    )

    for name in exporters:
        assert services[name]["environment"]["OPENRAG_OTEL_ENDPOINT"] == (
            "http://otel-collector:4317"
        )
        assert "observability-network" in services[name]["networks"]
