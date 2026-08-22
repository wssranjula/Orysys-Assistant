"""Conversation contract placeholder until persistent memory is introduced."""

from uuid import UUID

from fastapi import APIRouter

from orysys_assistant.domain.models import ConversationSnapshot

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


@router.get("/{conversation_id}", response_model=ConversationSnapshot)
async def get_conversation(conversation_id: UUID) -> ConversationSnapshot:
    return ConversationSnapshot(conversation_id=conversation_id)
