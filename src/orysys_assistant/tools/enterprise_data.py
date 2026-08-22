"""Fictional read-only enterprise records shared by mock MCP adapters."""

from typing import Any

EMPLOYEES = [
    {
        "employee_id": "EMP-001",
        "name": "Asha Silva",
        "department": "payments",
        "title": "Head of Payments Reliability",
    },
    {
        "employee_id": "EMP-002",
        "name": "Nimal Perera",
        "department": "technology",
        "title": "Platform Engineering Manager",
    },
    {
        "employee_id": "EMP-003",
        "name": "Maya Fernando",
        "department": "risk",
        "title": "Operational Risk Lead",
    },
]

SERVICES = [
    {
        "service_id": "SVC-PAY-001",
        "name": "payment-service",
        "owner": "Payments Reliability",
        "tier": "critical",
    },
    {
        "service_id": "SVC-ID-001",
        "name": "identity-service",
        "owner": "Platform Engineering",
        "tier": "critical",
    },
    {
        "service_id": "SVC-NOT-001",
        "name": "notification-service",
        "owner": "Digital Channels",
        "tier": "standard",
    },
]

INCIDENTS = [
    {
        "incident_id": "INC-2025-001",
        "service_id": "SVC-PAY-001",
        "date": "2025-02-14",
        "summary": "Payment connection-pool exhaustion",
    },
    {
        "incident_id": "INC-2025-002",
        "service_id": "SVC-PAY-001",
        "date": "2025-06-08",
        "summary": "Payment queue backlog",
    },
    {
        "incident_id": "INC-2025-003",
        "service_id": "SVC-ID-001",
        "date": "2025-09-19",
        "summary": "Identity token validation latency",
    },
]


def get_record(records: list[dict[str, Any]], key: str, value: str) -> dict[str, Any]:
    return next((dict(record) for record in records if record[key].lower() == value.lower()), {})


def search_records(records: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    normalized = query.lower()
    ignored = {"the", "a", "an", "of", "for", "find", "show", "who", "owns", "owner"}
    terms = [term for term in normalized.replace("?", "").split() if term not in ignored]
    return [
        dict(record)
        for record in records
        if normalized in " ".join(map(str, record.values())).lower()
        or any(term in " ".join(map(str, record.values())).lower() for term in terms)
    ]
