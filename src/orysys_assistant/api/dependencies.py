"""FastAPI dependency aliases."""

from typing import Annotated, cast

from fastapi import Depends, Request

from orysys_assistant.config import Settings


def get_request_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


SettingsDependency = Annotated[Settings, Depends(get_request_settings)]
