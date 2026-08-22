import json
from pathlib import Path
from uuid import uuid4

import yaml

from scripts.smoke_test import assert_safe_stream, parse_sse

ROOT = Path(__file__).parents[2]


def test_compose_declares_hardened_five_service_delivery_topology() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert set(services) == {"api", "ui", "postgres", "redis", "mcp-server"}
    assert services["api"]["depends_on"].keys() == {"postgres", "redis", "mcp-server"}
    assert services["ui"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert services["api"]["read_only"] is True
    assert services["ui"]["read_only"] is True
    assert services["mcp-server"]["read_only"] is True
    assert services["api"]["security_opt"] == ["no-new-privileges:true"]
    assert services["postgres"]["volumes"]
    assert services["api"]["volumes"] == ["runtime-data:/app/.data"]
    assert services["api"]["ports"][0].startswith("127.0.0.1:")
    assert services["ui"]["ports"][0].startswith("127.0.0.1:")
    assert compose["networks"]["backend"]["internal"] is True
    assert all("healthcheck" in services[name] for name in services)


def test_application_images_run_as_non_root_and_api_contains_scripts() -> None:
    api = (ROOT / "docker" / "Dockerfile.api").read_text(encoding="utf-8")
    ui = (ROOT / "docker" / "Dockerfile.ui").read_text(encoding="utf-8")
    mcp = (ROOT / "docker" / "Dockerfile.mcp").read_text(encoding="utf-8")

    assert "USER appuser" in api
    assert "USER appuser" in ui
    assert "USER appuser" in mcp
    assert "COPY scripts ./scripts" in api
    assert "uv sync --frozen --no-dev" in api
    assert 'ENV PATH="/app/.venv/bin:$PATH"' in api


def test_env_example_and_docker_context_exclude_local_secrets() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "LANGSMITH_TRACING=false" in example
    assert "LANGSMITH_API_KEY=\n" in example
    assert "OPENAI_API_KEY=\n" in example
    assert ".env" in dockerignore
    assert ".data" in dockerignore
    assert "tests" in dockerignore


def test_black_box_smoke_parser_validates_trace_citations_and_safe_metadata() -> None:
    trace_id = str(uuid4())
    conversation_id = str(uuid4())
    activity = {
        "event_type": "routing_completed",
        "request_id": trace_id,
        "conversation_id": conversation_id,
        "status": "completed",
        "message": "Selected direct route.",
        "metadata": {"route": "direct_knowledge"},
    }
    validation = {
        **activity,
        "event_type": "validation_completed",
        "message": "Validated.",
    }
    final = {
        "request_id": trace_id,
        "conversation_id": conversation_id,
        "status": "complete",
        "answer": "Grounded [1]",
        "citations": [{"citation_id": "1", "evidence_id": "ev_1"}],
        "warnings": [],
        "degraded": False,
    }
    body = "".join(
        (
            f"event: activity\ndata: {json.dumps(activity)}\n\n",
            f"event: activity\ndata: {json.dumps(validation)}\n\n",
            f"event: final\ndata: {json.dumps(final)}\n\n",
        )
    )

    parsed = parse_sse(body)
    observed = assert_safe_stream(parsed, require_citations=True)

    assert observed["request_id"] == trace_id
