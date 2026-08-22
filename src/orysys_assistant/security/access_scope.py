"""Build document access filters exclusively from a trusted identity."""

from orysys_assistant.config import Settings
from orysys_assistant.domain.models import Role
from orysys_assistant.security.models import AccessScope, UserIdentity


class AccessScopeService:
    def __init__(self, settings: Settings) -> None:
        self._organization_id = settings.organization_id
        self._namespace = settings.pinecone_namespace

    def build(self, identity: UserIdentity) -> AccessScope:
        access_levels: tuple[str, ...]
        departments: tuple[str, ...]
        if identity.role is Role.ADMINISTRATOR:
            access_levels = ("internal", "confidential", "restricted")
            departments = ()
        elif identity.role is Role.ANALYST:
            access_levels = ("internal", "confidential")
            departments = (identity.department, "all-employees")
        else:
            access_levels = ("internal",)
            departments = (identity.department, "all-employees")

        return AccessScope(
            organization_id=self._organization_id,
            namespace=self._namespace,
            allowed_access_levels=access_levels,
            allowed_departments=departments,
        )
