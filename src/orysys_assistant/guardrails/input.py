"""Defense-in-depth validation before conversation state or agents are touched."""

import re

from orysys_assistant.domain.errors import InvalidRequestError
from orysys_assistant.domain.models import ChatRequest
from orysys_assistant.guardrails.patterns import INJECTION_PATTERNS

_DISALLOWED_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class InputGuard:
    """Validate semantic constraints not expressible in the public Pydantic schema."""

    def validate(self, request: ChatRequest) -> None:
        message = request.message
        if _DISALLOWED_CONTROL_CHARACTERS.search(message):
            raise InvalidRequestError("The message contains unsupported control characters.")
        for pattern in INJECTION_PATTERNS:
            if pattern.search(message):
                raise InvalidRequestError(
                    "The message contains unsupported instruction-override content."
                )
