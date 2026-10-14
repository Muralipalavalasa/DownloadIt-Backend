from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.services.auth_service import (
    EmailAlreadyRegisteredError,
    authenticate_user,
    create_access_token,
    create_user,
)

router = APIRouter()


@router.post('/register', response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(request: RegisterRequest, db: Session = Depends(get_db)) -> User:
    try:
        return create_user(db, str(request.email), request.password)
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail='An account with this email already exists.') from exc


@router.post('/login', response_model=TokenResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = authenticate_user(db, str(request.email), request.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid email or password.',
            headers={'WWW-Authenticate': 'Bearer'},
        )
    return TokenResponse(access_token=create_access_token(user.id))


@router.get('/me', response_model=UserResponse)
def current_user(user: User = Depends(get_current_user)) -> User:
    return user