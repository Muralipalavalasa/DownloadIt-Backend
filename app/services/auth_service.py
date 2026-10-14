from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from pwdlib import PasswordHash
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import User

password_hasher = PasswordHash.recommended()


class EmailAlreadyRegisteredError(Exception):
    pass


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


def create_user(db: Session, email: str, password: str) -> User:
    user = User(email=email.casefold().strip(), password_hash=hash_password(password), is_active=True)
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise EmailAlreadyRegisteredError from exc
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(
        select(User).where(func.lower(User.email) == email.casefold().strip())
    )
    if user is None or not user.is_active or user.password_hash is None:
        return None
    return user if verify_password(password, user.password_hash) else None


def create_access_token(user_id: UUID) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode(
        {'sub': str(user_id), 'exp': expires_at},
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> UUID:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
        return UUID(payload['sub'])
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValueError('Invalid access token.') from exc


def get_user_by_id(db: Session, user_id: UUID) -> User | None:
    return db.get(User, user_id)