"""Conversation contract placeholder until persistent memory is introduced."""

from uuid import UUID

from fastapi import APIRouter

from orysys_assistant.api.dependencies import ConversationRepositoryDependency, IdentityDependency
from orysys_assistant.domain.models import ConversationSnapshot

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


@router.get("/{conversation_id}", response_model=ConversationSnapshot)
async def get_conversation(
    conversation_id: UUID,
    identity: IdentityDependency,
    repository: ConversationRepositoryDependency,
) -> ConversationSnapshot:
    record = await repository.get(conversation_id, identity.user_id)
    if record is None:
        return ConversationSnapshot(
            conversation_id=conversation_id,
            persistence=repository.persistence_name,
        )
    return ConversationSnapshot(
        conversation_id=record.conversation_id,
        messages=[
            {"role": message.role, "content": message.content} for message in record.messages
        ],
        summary=record.summary,
        evidence_ids=record.evidence_ids,
        persistence=repository.persistence_name,
    )
