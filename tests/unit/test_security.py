import asyncio

import pytest
from pydantic import BaseModel, ConfigDict

from orysys_assistant.config import Settings
from orysys_assistant.domain.errors import AuthorizationError, InvalidRequestError
from orysys_assistant.domain.models import Role
from orysys_assistant.security.access_scope import AccessScopeService
from orysys_assistant.security.authentication import AuthenticationService
from orysys_assistant.security.authorization import AuthorizationPolicy, Capability
from orysys_assistant.security.models import AccessScope, TrustedRequestContext, UserIdentity
from orysys_assistant.security.rate_limit import (
    BucketPolicy,
    InMemoryTokenBucket,
)
from orysys_assistant.tools.gateway import ToolGateway, ToolSpec


def identity(role: Role) -> UserIdentity:
    return UserIdentity(
        user_id=f"user-{role.value}",
        username=f"{role.value}@commercialbank.test",
        display_name=role.value.title(),
        role=role,
        department="payments",
    )


def context(role: Role) -> TrustedRequestContext:
    return TrustedRequestContext(
        identity=identity(role),
        access_scope=AccessScope(
            organization_id="commercial-bank",
            namespace="commercial-bank",
            allowed_access_levels=("internal",),
            allowed_departments=("payments",),
        ),
        rate_limit_remaining=5,
    )


def test_password_authentication_and_tokens_map_to_trusted_users() -> None:
    service = AuthenticationService(Settings(_env_file=None))

    viewer, token = service.authenticate_password("viewer@commercialbank.test", "ViewerDemo!2026")

    assert viewer.role is Role.VIEWER
    assert service.authenticate_token(token) == viewer
    assert "ViewerDemo!2026" not in repr(service._records)  # noqa: SLF001


@pytest.mark.parametrize(
    ("role", "capability", "allowed"),
    [
        (Role.VIEWER, Capability.KNOWLEDGE_SEARCH, True),
        (Role.VIEWER, Capability.STRUCTURED_ANALYSIS, False),
        (Role.VIEWER, Capability.MCP_READ, False),
        (Role.ANALYST, Capability.STRUCTURED_ANALYSIS, True),
        (Role.ANALYST, Capability.MCP_READ, True),
        (Role.ANALYST, Capability.ADMIN_TOOLS, False),
        (Role.ADMINISTRATOR, Capability.ADMIN_TOOLS, True),
        (Role.ADMINISTRATOR, Capability.RESTRICTED_DOCUMENTS, True),
    ],
)
def test_central_role_policy(role: Role, capability: Capability, allowed: bool) -> None:
    assert AuthorizationPolicy().is_allowed(role, capability) is allowed


def test_access_scope_is_derived_from_identity() -> None:
    service = AccessScopeService(Settings(_env_file=None))

    viewer = service.build(identity(Role.VIEWER))
    analyst = service.build(identity(Role.ANALYST))
    administrator = service.build(identity(Role.ADMINISTRATOR))

    assert viewer.allowed_access_levels == ("internal",)
    assert analyst.allowed_access_levels == ("internal", "confidential")
    assert administrator.allowed_access_levels == ("internal", "confidential", "restricted")
    assert administrator.allowed_departments == ()
    assert viewer.retrieval_filter()["organization"] == {"$eq": "commercial-bank"}
    assert viewer.retrieval_filter()["department"] == {"$in": ["payments", "all-employees"]}


class QueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str


@pytest.mark.asyncio
async def test_gateway_denies_viewer_before_tool_handler() -> None:
    calls = 0

    async def handler(_: BaseModel, __: TrustedRequestContext) -> dict[str, bool]:
        nonlocal calls
        calls += 1
        return {"executed": True}

    gateway = ToolGateway(AuthorizationPolicy())
    gateway.register(
        ToolSpec("structured_analysis", Capability.STRUCTURED_ANALYSIS, QueryInput, handler)
    )

    with pytest.raises(AuthorizationError):
        await gateway.execute(
            "structured_analysis", {"query": "count incidents"}, context(Role.VIEWER)
        )
    assert calls == 0


@pytest.mark.asyncio
async def test_gateway_rejects_server_controlled_fields() -> None:
    async def handler(_: BaseModel, __: TrustedRequestContext) -> dict[str, bool]:
        return {"executed": True}

    gateway = ToolGateway(AuthorizationPolicy())
    gateway.register(ToolSpec("knowledge_search", Capability.KNOWLEDGE_SEARCH, QueryInput, handler))

    with pytest.raises(InvalidRequestError) as exc_info:
        await gateway.execute(
            "knowledge_search",
            {"query": "policy", "nested": {"namespace": "other-bank"}},
            context(Role.ADMINISTRATOR),
        )
    assert exc_info.value.details == {"fields": ["namespace"]}


@pytest.mark.asyncio
async def test_memory_token_bucket_is_atomic_under_concurrency() -> None:
    policies = {role: BucketPolicy(capacity=5, refill_per_minute=0.001) for role in Role}
    limiter = InMemoryTokenBucket(policies)

    results = await asyncio.gather(*(limiter.consume("same-user", Role.VIEWER) for _ in range(20)))

    assert sum(result.allowed for result in results) == 5
    assert all(result.remaining >= 0 for result in results)
