"""Immutable trusted-security models that are never populated from request bodies."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from orysys_assistant.domain.models import Role


class TrustedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UserIdentity(TrustedModel):
    user_id: str
    username: str
    display_name: str
    role: Role
    department: str


class AccessScope(TrustedModel):
    organization_id: str
    namespace: str
    allowed_access_levels: tuple[str, ...]
    allowed_departments: tuple[str, ...]

    def retrieval_filter(self) -> dict[str, Any]:
        filters: dict[str, Any] = {
            "organization": {"$eq": self.organization_id},
            "access_level": {"$in": list(self.allowed_access_levels)},
        }
        if self.allowed_departments:
            filters["department"] = {"$in": list(self.allowed_departments)}
        return filters


class TrustedRequestContext(TrustedModel):
    identity: UserIdentity
    access_scope: AccessScope
    rate_limit_remaining: int
