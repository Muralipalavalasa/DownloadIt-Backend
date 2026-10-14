from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.services.auth_service import decode_access_token, get_user_by_id

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='Invalid or missing authentication credentials.',
        headers={'WWW-Authenticate': 'Bearer'},
    )
    if credentials is None:
        raise unauthorized
    try:
        user_id: UUID = decode_access_token(credentials.credentials)
    except ValueError as exc:
        raise unauthorized from exc
    user = get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return user