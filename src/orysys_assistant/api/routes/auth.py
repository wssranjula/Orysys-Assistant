"""POC credential exchange for the four hardcoded users."""

from fastapi import APIRouter

from orysys_assistant.api.dependencies import AuthenticationDependency
from orysys_assistant.domain.models import LoginRequest, TokenResponse

router = APIRouter(prefix="/v1/auth", tags=["authentication"])


@router.post("/token", response_model=TokenResponse)
async def issue_token(
    payload: LoginRequest,
    authentication: AuthenticationDependency,
) -> TokenResponse:
    identity, token = authentication.authenticate_password(
        payload.username,
        payload.password.get_secret_value(),
    )
    return TokenResponse(
        access_token=token,
        user_id=identity.user_id,
        display_name=identity.display_name,
        role=identity.role,
    )
