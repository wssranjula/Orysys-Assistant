"""Defense-in-depth validation before conversation state or agents are touched."""

import re

from orysys_assistant.domain.errors import InvalidRequestError
from orysys_assistant.domain.models import ChatRequest

_DISALLOWED_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class InputGuard:
    """Validate semantic constraints not expressible in the public Pydantic schema."""

    def validate(self, request: ChatRequest) -> None:
        if _DISALLOWED_CONTROL_CHARACTERS.search(request.message):
            raise InvalidRequestError("The message contains unsupported control characters.")
