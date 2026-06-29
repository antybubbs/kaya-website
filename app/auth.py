from passlib.context import CryptContext
from itsdangerous import URLSafeTimedSerializer
from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse
from .config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
serializer = URLSafeTimedSerializer(settings.secret_key)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def get_admin_credentials():
    return settings.admin_email, settings.admin_password


def require_admin(request: Request):
    if request.session.get("admin_authenticated"):
        return True
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def login_user(request: Request):
    request.session["admin_authenticated"] = True


def logout_user(request: Request):
    request.session.pop("admin_authenticated", None)
