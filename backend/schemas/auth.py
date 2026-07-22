from typing import Optional

from pydantic import BaseModel


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: Optional[str] = "user"
    admin_code: Optional[str] = None


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
