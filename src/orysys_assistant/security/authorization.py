"""The single role-to-capability policy implementation."""

from enum import StrEnum

from langsmith import traceable

from orysys_assistant.domain.errors import AuthorizationError
from orysys_assistant.domain.models import Role
from orysys_assistant.observability.agent_tracing import app_span_tags
from orysys_assistant.observability.logging import get_logger
from orysys_assistant.security.models import UserIdentity

logger = get_logger()


class Capability(StrEnum):
    CHAT = "chat"
    KNOWLEDGE_SEARCH = "knowledge_search"
    STRUCTURED_ANALYSIS = "structured_analysis"
    MCP_READ = "mcp_read"
    ADMIN_TOOLS = "admin_tools"
    RESTRICTED_DOCUMENTS = "restricted_documents"


ROLE_CAPABILITIES: dict[Role, frozenset[Capability]] = {
    Role.VIEWER: frozenset({Capability.CHAT, Capability.KNOWLEDGE_SEARCH}),
    Role.ANALYST: frozenset(
        {
            Capability.CHAT,
            Capability.KNOWLEDGE_SEARCH,
            Capability.STRUCTURED_ANALYSIS,
            Capability.MCP_READ,
        }
    ),
    Role.ADMINISTRATOR: frozenset(Capability),
}


class AuthorizationPolicy:
    def is_allowed(self, role: Role, capability: Capability) -> bool:
        return capability in ROLE_CAPABILITIES[role]

    @traceable(name="authorization-decision", run_type="tool", tags=app_span_tags("auth"))
    def require(self, identity: UserIdentity, capability: Capability) -> None:
        allowed = self.is_allowed(identity.role, capability)
        logger.info(
            "authorization_decision",
            user_id=identity.user_id,
            role=identity.role.value,
            capability=capability.value,
            result="allowed" if allowed else "denied",
        )
        if not allowed:
            raise AuthorizationError("You are not authorized to perform this operation.")
