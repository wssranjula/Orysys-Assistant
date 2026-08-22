"""Fail-fast checks for a clean, reproducible public assessment package."""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
REQUIRED_PATHS = (
    ".dockerignore",
    ".env.example",
    ".github/workflows/ci.yml",
    "README.md",
    "docker-compose.yml",
    "docs/architecture.md",
    "docs/assumptions-and-tradeoffs.md",
    "docs/demo-script.md",
    "docs/deployment.md",
    "docs/README.md",
    "docs/observability-and-evaluation.md",
    "data/golden_evaluation_report.json",
    "data/hard_research_questions.json",
)
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\blsv2_[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bpcsk_[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def git_files() -> list[str]:
    output = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    return [line for line in output.splitlines() if line]


def main() -> None:
    failures: list[str] = []
    for relative in REQUIRED_PATHS:
        if not (ROOT / relative).is_file():
            failures.append(f"missing required delivery file: {relative}")

    tracked = git_files()
    forbidden = {".env", ".streamlit/secrets.toml"} & set(tracked)
    if forbidden:
        failures.append(f"secret-bearing local files are tracked: {sorted(forbidden)}")

    for relative in tracked:
        path = ROOT / relative
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(content):
                failures.append(f"possible credential in tracked file: {relative}")
                break

    report_path = ROOT / "data" / "golden_evaluation_report.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        metrics = report["metrics"]
        if metrics["citation_validity_rate"] != 1:
            failures.append("golden citation validity is below 100%")
        if metrics["unauthorized_evidence_rate"] != 0:
            failures.append("golden unauthorized-evidence rate is nonzero")

    compose = subprocess.run(
        ["docker", "compose", "config", "--quiet"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if compose.returncode:
        failures.append("docker compose configuration is invalid")

    if failures:
        raise SystemExit("Public readiness failed:\n- " + "\n- ".join(failures))
    print(f"Public readiness passed for {len(tracked)} tracked files.")
    print("Remote visibility is a manual repository-host setting and was not changed.")


if __name__ == "__main__":
    main()
