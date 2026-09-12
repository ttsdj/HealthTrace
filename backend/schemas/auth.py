import re
from typing import Optional

from pydantic import BaseModel, field_validator

# Usernames feed Redis cache keys and audit metadata: keep them to a safe
# charset so they cannot collide or inject separator characters.
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: Optional[str] = "user"
    admin_code: Optional[str] = None

    @field_validator("username")
    @classmethod
    def _validate_username(cls, value: str) -> str:
        if not _USERNAME_PATTERN.fullmatch(value or ""):
            raise ValueError(
                "username must be 3-32 characters of letters, digits, dot, dash or underscore"
            )
        return value

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        if len(value or "") < 8:
            raise ValueError("password must be at least 8 characters")
        return value


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str
    tenant_id: Optional[str] = None
    patient_id: Optional[str] = None


class CurrentUserResponse(BaseModel):
    username: str
    role: str
    tenant_id: Optional[str] = None
    patient_id: Optional[str] = None
