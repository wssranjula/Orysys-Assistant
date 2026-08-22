"""Controlled structured analysis operations without arbitrary code execution."""

from collections import Counter
from enum import StrEnum
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orysys_assistant.domain.errors import InvalidRequestError
from orysys_assistant.security.authorization import Capability
from orysys_assistant.security.models import TrustedRequestContext
from orysys_assistant.tools.gateway import ToolSpec


class AnalysisOperation(StrEnum):
    COUNT_BY = "count_by"
    GROUP_BY = "group_by"
    TREND_BY_DATE = "trend_by_date"
    TOP_VALUES = "top_values"
    PERCENTAGE = "percentage"


class PythonAnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: AnalysisOperation
    records: list[dict[str, Any]] = Field(max_length=5_000)
    field: str = Field(min_length=1, max_length=100)
    date_field: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def date_field_required_for_trend(self) -> "PythonAnalysisInput":
        if self.operation is AnalysisOperation.TREND_BY_DATE and not self.date_field:
            raise ValueError("date_field is required for trend_by_date")
        return self


class ControlledPythonAnalysisTool:
    def __init__(self, max_records: int) -> None:
        self._max_records = max_records

    async def __call__(
        self, parameters: BaseModel, context: TrustedRequestContext
    ) -> dict[str, Any]:
        request = cast(PythonAnalysisInput, parameters)
        if len(request.records) > self._max_records:
            raise InvalidRequestError("The analysis record limit was exceeded.")
        missing = [index for index, row in enumerate(request.records) if request.field not in row]
        if missing:
            raise InvalidRequestError(
                "Analysis records are missing the requested field.",
                details={"record_indexes": missing[:20]},
            )
        field = (
            request.date_field
            if request.operation is AnalysisOperation.TREND_BY_DATE
            else request.field
        )
        if field is None:
            raise InvalidRequestError("A date field is required for trend analysis.")
        values = [str(row[field]) for row in request.records if field in row]
        counts = Counter(values)
        if request.operation is AnalysisOperation.TREND_BY_DATE:
            ordered = sorted(counts.items())[: request.limit]
            results = [{"date": value, "count": count} for value, count in ordered]
        else:
            ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[: request.limit]
            results = [
                {
                    "value": value,
                    "count": count,
                    **(
                        {"percentage": round(count * 100 / len(values), 2)}
                        if request.operation is AnalysisOperation.PERCENTAGE and values
                        else {}
                    ),
                }
                for value, count in ordered
            ]
        return {
            "operation": request.operation.value,
            "rows_processed": len(request.records),
            "results": results,
            "warnings": [],
        }


def python_analysis_spec(max_records: int) -> ToolSpec:
    return ToolSpec(
        name="structured_analysis",
        capability=Capability.STRUCTURED_ANALYSIS,
        input_model=PythonAnalysisInput,
        handler=ControlledPythonAnalysisTool(max_records),
        timeout_seconds=10,
    )
