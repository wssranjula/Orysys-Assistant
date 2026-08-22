import json
from pathlib import Path

GOLDEN_PATH = Path(__file__).parents[2] / "data" / "golden_questions.json"


def test_golden_dataset_contains_the_ten_frozen_scenarios() -> None:
    dataset = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    cases = dataset["cases"]

    assert dataset["schema_version"] == "1.0"
    assert len(cases) == 10
    assert [case["id"] for case in cases] == [f"GQ-{index:03}" for index in range(1, 11)]


def test_each_golden_case_has_testable_expectations() -> None:
    cases = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["cases"]
    valid_roles = {"viewer", "analyst", "administrator"}
    valid_statuses = {"complete", "partial", "insufficient_evidence", "failed"}

    for case in cases:
        assert case["role"] in valid_roles
        assert case["expected_status"] in valid_statuses
        assert case["question"].strip()
        assert len(case["assertions"]) >= 3
