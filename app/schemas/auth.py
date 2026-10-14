import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator


def normalize_email(value: object) -> object:
    return value.strip().casefold() if isinstance(value, str) else value


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator('email', mode='before')
    @classmethod
    def normalize_address(cls, value: object) -> object:
        return normalize_email(value)

    @field_validator('password')
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        if len(value) < 12:
            raise ValueError('Password must be at least 12 characters long.')
        if not re.search(r'[a-z]', value) or not re.search(r'[A-Z]', value):
            raise ValueError('Password must contain uppercase and lowercase letters.')
        if not re.search(r'\d', value) or not re.search(r'[^A-Za-z0-9]', value):
            raise ValueError('Password must contain a number and a symbol.')
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator('email', mode='before')
    @classmethod
    def normalize_address(cls, value: object) -> object:
        return normalize_email(value)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    created_at: datetime
    is_active: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'