from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db

router = APIRouter()


@router.get('/health')
def health_check(db: Session = Depends(get_db)) -> dict:
    try:
        db.execute(text('SELECT 1'))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                'status': 'unavailable',
                'service': settings.app_name,
                'database': 'unavailable',
            },
        ) from exc

    return {
        'status': 'ok',
        'service': settings.app_name,
        'database': 'connected',
    }
