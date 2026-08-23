"""POC-only hardcoded users with salted PBKDF2 password verification."""

import hashlib
import hmac
from dataclasses import dataclass

from orysys_assistant.config import Settings
from orysys_assistant.domain.errors import AuthenticationError
from orysys_assistant.domain.models import Role
from orysys_assistant.security.models import UserIdentity

PBKDF2_ITERATIONS = 310_000


@dataclass(frozen=True, slots=True)
class UserRecord:
    identity: UserIdentity
    password_salt: str
    password_digest: str
    bearer_token: str


class AuthenticationService:
    def __init__(self, settings: Settings) -> None:
        self._records = (
            UserRecord(
                identity=UserIdentity(
                    user_id="user-viewer-01",
                    username="viewer@commercialbank.test",
                    display_name="Vina Perera",
                    role=Role.VIEWER,
                    department="retail-banking",
                ),
                password_salt="viewer-salt-v1",
                password_digest=(
                    "dfce1ad9c743fa4f54c0917a174b666aeca3f62ffca8ce84a5dcfb425008aef5"
                ),
                bearer_token=settings.auth_viewer_token,
            ),
            UserRecord(
                identity=UserIdentity(
                    user_id="user-analyst-01",
                    username="analyst@commercialbank.test",
                    display_name="Arun Silva",
                    role=Role.ANALYST,
                    department="payments",
                ),
                password_salt="analyst-salt-v1",
                password_digest=(
                    "869aeda0d5567cd7f44604a829317b645aed16d1eed3d79a5d4b2fb770f7a99d"
                ),
                bearer_token=settings.auth_analyst_token,
            ),
            UserRecord(
                identity=UserIdentity(
                    user_id="user-admin-01",
                    username="admin@commercialbank.test",
                    display_name="Maya Fernando",
                    role=Role.ADMINISTRATOR,
                    department="technology",
                ),
                password_salt="admin-salt-v1",
                password_digest=(
                    "eace0a8960277fa56e1200134575a466e36687dca4ea6a196467e1e3935e5f35"
                ),
                bearer_token=settings.auth_admin_token,
            ),
            UserRecord(
                identity=UserIdentity(
                    user_id="user-admin-approver-01",
                    username="approver@commercialbank.test",
                    display_name="Nimal Jayasinghe",
                    role=Role.ADMINISTRATOR,
                    department="risk",
                ),
                password_salt="admin-approver-salt-v1",
                password_digest=(
                    "ad79749a55107da77268d66ef5d7af0fa4a70961ffb8dfeb9459a79afd816a2d"
                ),
                bearer_token=settings.auth_approver_token,
            ),
        )
        self._by_username = {record.identity.username: record for record in self._records}

    @staticmethod
    def _password_digest(password: str, salt: str) -> str:
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            PBKDF2_ITERATIONS,
        )
        return digest.hex()

    def authenticate_password(self, username: str, password: str) -> tuple[UserIdentity, str]:
        record = self._by_username.get(username.strip().lower())
        if record is None:
            # Run equivalent work to reduce username-enumeration timing differences.
            self._password_digest(password, "unknown-user-salt-v1")
            raise AuthenticationError("Invalid username or password.")

        candidate = self._password_digest(password, record.password_salt)
        if not hmac.compare_digest(candidate, record.password_digest):
            raise AuthenticationError("Invalid username or password.")
        return record.identity, record.bearer_token

    def authenticate_token(self, token: str) -> UserIdentity:
        matched: UserIdentity | None = None
        for record in self._records:
            if hmac.compare_digest(token, record.bearer_token):
                matched = record.identity
        if matched is None:
            raise AuthenticationError("Invalid or expired bearer token.")
        return matched
