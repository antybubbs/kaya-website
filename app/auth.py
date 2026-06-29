from datetime import datetime, timedelta
import json
from passlib.context import CryptContext
from itsdangerous import URLSafeTimedSerializer
from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from .config import settings
from .security import TOTP2FA, check_rate_limit, record_login_attempt, clear_login_attempts, get_client_ip
from . import models, crud

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
serializer = URLSafeTimedSerializer(settings.secret_key)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def get_admin_credentials():
    """Get admin credentials from environment."""
    return settings.admin_email, settings.admin_password


def get_or_create_admin_user(db: Session) -> models.AdminUser:
    """Get admin user from database, creating if necessary."""
    admin = db.query(models.AdminUser).filter(
        models.AdminUser.email == settings.admin_email
    ).first()
    
    if not admin:
        admin = models.AdminUser(
            email=settings.admin_email,
            password_hash=get_password_hash(settings.admin_password),
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
    
    return admin


def check_admin_locked(admin: models.AdminUser) -> bool:
    """Check if admin account is locked."""
    if admin.locked_until and datetime.utcnow() < admin.locked_until:
        return True
    return False


def unlock_admin(db: Session, admin: models.AdminUser):
    """Unlock admin account."""
    admin.locked_until = None
    admin.failed_login_attempts = 0
    db.commit()


def lock_admin(db: Session, admin: models.AdminUser):
    """Lock admin account."""
    admin.locked_until = datetime.utcnow() + timedelta(
        seconds=settings.account_lockout_duration
    )
    db.commit()


def record_failed_login(db: Session, admin: models.AdminUser):
    """Record failed login attempt."""
    admin.failed_login_attempts += 1
    if admin.failed_login_attempts >= settings.max_login_attempts:
        lock_admin(db, admin)
    else:
        db.commit()


def record_successful_login(db: Session, admin: models.AdminUser):
    """Record successful login."""
    admin.last_login = datetime.utcnow()
    admin.failed_login_attempts = 0
    admin.locked_until = None
    db.commit()


def setup_2fa(db: Session, admin: models.AdminUser) -> tuple[str, list[str]]:
    """
    Setup 2FA for admin user.
    Returns (provisioning_uri, backup_codes)
    """
    secret = TOTP2FA.generate_secret()
    backup_codes = TOTP2FA.get_backup_codes()
    
    admin.totp_secret = secret
    admin.backup_codes = json.dumps(backup_codes)
    admin.totp_enabled = False  # Not enabled until verified
    db.commit()
    
    provisioning_uri = TOTP2FA.get_provisioning_uri(secret, admin.email)
    return provisioning_uri, backup_codes


def verify_2fa_setup(db: Session, admin: models.AdminUser, token: str) -> bool:
    """Verify 2FA setup token."""
    if not admin.totp_secret:
        return False
    
    if not TOTP2FA.verify_token(admin.totp_secret, token):
        return False
    
    admin.totp_enabled = True
    db.commit()
    return True


def verify_2fa_token(admin: models.AdminUser, token: str) -> bool:
    """Verify 2FA token during login."""
    if not admin.totp_secret or not admin.totp_enabled:
        return True  # 2FA not enabled
    
    if not token:
        return False
    
    # Try main token
    if TOTP2FA.verify_token(admin.totp_secret, token):
        return True
    
    # Try backup codes
    if admin.backup_codes:
        try:
            backup_codes = json.loads(admin.backup_codes)
            if token in backup_codes:
                backup_codes.remove(token)
                admin.backup_codes = json.dumps(backup_codes)
                return True
        except (json.JSONDecodeError, ValueError):
            pass
    
    return False


def require_admin(request: Request):
    """Check if user is authenticated as admin."""
    if request.session.get("admin_authenticated"):
        return True
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def login_user(request: Request, email: str = ""):
    """Set admin authentication session."""
    request.session["admin_authenticated"] = True
    request.session["login_time"] = datetime.utcnow().isoformat()
    if email:
        request.session["admin_email"] = email


def logout_user(request: Request):
    """Clear admin authentication session."""
    request.session.pop("admin_authenticated", None)
    request.session.pop("login_time", None)
    request.session.pop("admin_email", None)
    request.session.pop("pending_2fa", None)


def check_session_timeout(request: Request) -> bool:
    """Check if session has timed out."""
    login_time_str = request.session.get("login_time")
    if not login_time_str:
        return True
    
    try:
        login_time = datetime.fromisoformat(login_time_str)
        if datetime.utcnow() - login_time > timedelta(seconds=settings.session_timeout):
            logout_user(request)
            return True
    except ValueError:
        return True
    
    return False
