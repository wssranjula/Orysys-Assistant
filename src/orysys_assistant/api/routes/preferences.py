"""Explicit, owner-isolated long-term preference memory endpoints."""

from fastapi import APIRouter, status

from orysys_assistant.api.dependencies import ConversationRepositoryDependency, IdentityDependency
from orysys_assistant.domain.errors import InvalidRequestError
from orysys_assistant.domain.models import (
    PreferenceListResponse,
    PreferenceResponse,
    PreferenceWriteRequest,
)

router = APIRouter(prefix="/v1/memory/preferences", tags=["memory"])


@router.get("", response_model=PreferenceListResponse)
async def list_preferences(
    identity: IdentityDependency,
    repository: ConversationRepositoryDependency,
) -> PreferenceListResponse:
    preferences = await repository.list_preferences(identity.user_id)
    return PreferenceListResponse(
        preferences=[
            PreferenceResponse(key=item.key, value=item.value, updated_at=item.updated_at)
            for item in preferences
        ],
        persistence=repository.persistence_name,
    )


@router.put("/{key}", response_model=PreferenceResponse)
async def remember_preference(
    key: str,
    payload: PreferenceWriteRequest,
    identity: IdentityDependency,
    repository: ConversationRepositoryDependency,
) -> PreferenceResponse:
    if not payload.explicit:
        raise InvalidRequestError("Long-term memory requires explicit user consent.")
    if key != payload.key:
        raise InvalidRequestError("Preference path and body keys must match.")
    value = payload.value.strip()
    if not value:
        raise InvalidRequestError("Preference value must contain text.")
    item = await repository.upsert_preference(identity.user_id, payload.key, value)
    return PreferenceResponse(key=item.key, value=item.value, updated_at=item.updated_at)


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def forget_preference(
    key: str,
    identity: IdentityDependency,
    repository: ConversationRepositoryDependency,
) -> None:
    await repository.delete_preference(identity.user_id, key)
