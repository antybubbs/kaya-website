# Security Integration Guide for main.py

This file contains code snippets and implementation guidance for integrating security features into main.py.

## 1. Add Imports (at the top of main.py)

```python
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.security import (
    TOTP2FA, 
    check_rate_limit, 
    record_login_attempt, 
    clear_login_attempts, 
    get_client_ip,
    generate_csrf_token,
    verify_csrf_token,
    PasswordValidator
)
from app.auth import (
    verify_password,
    get_password_hash,
    get_admin_credentials,
    get_or_create_admin_user,
    check_admin_locked,
    lock_admin,
    unlock_admin,
    record_failed_login,
    record_successful_login,
    check_session_timeout,
    setup_2fa,
    verify_2fa_setup,
    verify_2fa_token,
    login_user,
    logout_user,
)
from app.config import settings
from app import crud
```

## 2. Add Security Middleware (in create_app function)

```python
# Add after app = FastAPI()
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Add security headers to all responses"""
    response = await call_next(request)
    
    # Prevent MIME type sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"
    
    # Prevent clickjacking
    response.headers["X-Frame-Options"] = "DENY"
    
    # Enable XSS protection
    response.headers["X-XSS-Protection"] = "1; mode=block"
    
    # HSTS header
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    
    # CSP header
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self';"
    
    # Referrer policy
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    
    # Remove server header
    response.headers.pop("server", None)
    
    return response


@app.middleware("http")
async def session_timeout_middleware(request: Request, call_next):
    """Check and enforce session timeout"""
    # Skip timeout check for non-admin routes
    if request.url.path.startswith("/admin") and request.session.get("admin_authenticated"):
        if check_session_timeout(request):
            return RedirectResponse(url="/admin/login?expired=1", status_code=302)
    
    return await call_next(request)


@app.middleware("http")
async def cors_options_middleware(request: Request, call_next):
    """Handle CORS preflight requests"""
    if request.method == "OPTIONS":
        return Response(
            headers={
                "Access-Control-Allow-Origin": request.headers.get("origin", "*"),
                "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
                "Access-Control-Max-Age": "3600",
            }
        )
    return await call_next(request)
```

## 3. Update Admin Login Endpoint

Replace the existing `/admin/login` POST endpoint with:

```python
@app.post("/admin/login")
async def admin_login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """Admin login with rate limiting and 2FA support"""
    
    client_ip = get_client_ip(request)
    
    # Check rate limiting
    allowed, message = check_rate_limit(client_ip)
    if not allowed:
        return templates.TemplateResponse(
            "admin_login.html",
            {
                "request": request,
                "error": f"Too many login attempts. {message}",
            },
        )
    
    # Get or create admin user
    admin = crud.get_admin_by_email(db, email.lower())
    if not admin:
        # Fall back to environment credentials for initial setup
        env_email, env_password = get_admin_credentials()
        if email.lower() == env_email.lower():
            admin = get_or_create_admin_user(db)
        else:
            record_login_attempt(client_ip)
            return templates.TemplateResponse(
                "admin_login.html",
                {"request": request, "error": "Invalid email or password"},
            )
    
    # Check account lock
    if check_admin_locked(admin):
        remaining_time = (admin.locked_until - datetime.utcnow()).total_seconds()
        minutes = int(remaining_time / 60) + 1
        return templates.TemplateResponse(
            "admin_login.html",
            {
                "request": request,
                "error": f"Account locked. Try again in {minutes} minute(s).",
            },
        )
    
    # Verify password
    if not verify_password(password, admin.password_hash):
        record_failed_login(db, admin)
        record_login_attempt(client_ip)
        return templates.TemplateResponse(
            "admin_login.html",
            {"request": request, "error": "Invalid email or password"},
        )
    
    # Password correct - check if 2FA is required
    if admin.totp_enabled and settings.enable_2fa:
        # Store admin ID in session for 2FA verification
        request.session["pending_2fa_user_id"] = admin.id
        request.session["pending_2fa_email"] = admin.email
        clear_login_attempts(client_ip)
        return RedirectResponse(url="/admin/verify-2fa", status_code=302)
    
    # 2FA not enabled - complete login
    record_successful_login(db, admin)
    clear_login_attempts(client_ip)
    login_user(request)
    
    return RedirectResponse(url="/admin/pages", status_code=302)
```

## 4. Add 2FA Verification Endpoint

Add this new endpoint:

```python
@app.get("/admin/verify-2fa")
async def verify_2fa_get(request: Request):
    """Display 2FA verification form"""
    if not request.session.get("pending_2fa_user_id"):
        return RedirectResponse(url="/admin/login", status_code=302)
    
    return templates.TemplateResponse(
        "admin_verify_2fa.html",
        {"request": request, "email": request.session.get("pending_2fa_email")},
    )


@app.post("/admin/verify-2fa")
async def verify_2fa_post(
    request: Request,
    token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Verify 2FA token"""
    user_id = request.session.get("pending_2fa_user_id")
    if not user_id:
        return RedirectResponse(url="/admin/login", status_code=302)
    
    admin = db.query(models.AdminUser).filter(models.AdminUser.id == user_id).first()
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)
    
    # Verify 2FA token
    if not verify_2fa_token(admin, token):
        return templates.TemplateResponse(
            "admin_verify_2fa.html",
            {
                "request": request,
                "email": admin.email,
                "error": "Invalid or expired code",
            },
        )
    
    # 2FA successful
    record_successful_login(db, admin)
    request.session.pop("pending_2fa_user_id", None)
    request.session.pop("pending_2fa_email", None)
    login_user(request)
    
    return RedirectResponse(url="/admin/pages", status_code=302)
```

## 5. Add 2FA Setup Endpoints

Add these new endpoints:

```python
@app.get("/admin/2fa/setup")
async def setup_2fa_get(request: Request, db: Session = Depends(get_db)):
    """Display 2FA setup form"""
    require_admin(request)
    
    admin = db.query(models.AdminUser).filter(
        models.AdminUser.email == request.session.get("admin_email", "")
    ).first()
    
    if not admin:
        raise HTTPException(status_code=404, detail="Admin user not found")
    
    if admin.totp_enabled:
        return RedirectResponse(url="/admin/settings?section=security", status_code=302)
    
    # Generate new secret
    provisioning_uri, backup_codes = setup_2fa(db, admin)
    qr_code = TOTP2FA.get_qr_code(provisioning_uri)
    
    # Store backup codes in session temporarily
    request.session["2fa_backup_codes"] = backup_codes
    
    return templates.TemplateResponse(
        "admin_2fa_setup.html",
        {
            "request": request,
            "qr_code": qr_code,
            "secret": admin.totp_secret,
            "backup_codes": backup_codes,
        },
    )


@app.post("/admin/2fa/setup")
async def setup_2fa_post(
    request: Request,
    token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Verify 2FA setup token"""
    require_admin(request)
    
    admin = db.query(models.AdminUser).filter(
        models.AdminUser.email == request.session.get("admin_email", "")
    ).first()
    
    if not admin or not admin.totp_secret:
        raise HTTPException(status_code=400, detail="Setup not started")
    
    if not verify_2fa_setup(db, admin, token):
        return templates.TemplateResponse(
            "admin_2fa_setup.html",
            {
                "request": request,
                "secret": admin.totp_secret,
                "backup_codes": request.session.get("2fa_backup_codes", []),
                "error": "Invalid code. Please try again.",
            },
        )
    
    # 2FA enabled successfully
    request.session.pop("2fa_backup_codes", None)
    
    return templates.TemplateResponse(
        "admin_2fa_success.html",
        {
            "request": request,
            "message": "2FA enabled successfully",
        },
    )


@app.post("/admin/2fa/disable")
async def disable_2fa_post(
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """Disable 2FA for current admin"""
    require_admin(request)
    
    admin = db.query(models.AdminUser).filter(
        models.AdminUser.email == request.session.get("admin_email", "")
    ).first()
    
    if not admin or not verify_password(password, admin.password_hash):
        return templates.TemplateResponse(
            "admin_settings.html",
            {
                "request": request,
                "error": "Incorrect password",
            },
        )
    
    admin.totp_enabled = False
    admin.totp_secret = None
    admin.backup_codes = None
    db.commit()
    
    return RedirectResponse(url="/admin/settings?section=security", status_code=302)
```

## 6. Add CSRF Token to Admin Login Form

In `admin_login.html`, add CSRF token:

```html
<form method="post" action="/admin/login">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <!-- other form fields -->
</form>
```

## 7. Update Admin Session Storage

In the login endpoint, store admin email for reference:

```python
request.session["admin_email"] = admin.email
```

## 8. Database Migration (Optional)

If you have existing admin data, run this to initialize AdminUser table:

```python
# Run once after deploying:
from app.database import engine
from app.models import Base
from app.auth import get_or_create_admin_user
from sqlalchemy.orm import Session

Base.metadata.create_all(bind=engine)

# Initialize admin user
db = Session(engine)
get_or_create_admin_user(db)
db.close()
```

## 9. Required Templates

Create two new templates:

### `admin_verify_2fa.html`
```html
{% extends "layout.html" %}

{% block content %}
<div class="container login-container">
    <h1>Two-Factor Authentication</h1>
    {% if error %}
    <div class="alert alert-error">{{ error }}</div>
    {% endif %}
    
    <form method="post" action="/admin/verify-2fa">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <div class="form-group">
            <label for="token">Enter 6-digit code from your authenticator app:</label>
            <input type="text" id="token" name="token" maxlength="6" pattern="\d{6}" required autofocus>
        </div>
        <div class="form-group">
            <label for="backup">Or enter a backup code:</label>
            <input type="text" id="backup" name="token" placeholder="xxxx-xxxx-xxxx">
        </div>
        <button type="submit" class="btn btn-primary">Verify</button>
    </form>
</div>
{% endblock %}
```

### `admin_2fa_setup.html`
```html
{% extends "layout.html" %}

{% block content %}
<div class="container setup-container">
    <h1>Setup Two-Factor Authentication</h1>
    
    {% if error %}
    <div class="alert alert-error">{{ error }}</div>
    {% endif %}
    
    <p>Scan this QR code with your authenticator app:</p>
    <div class="qr-code">
        <img src="data:image/png;base64,{{ qr_code }}" alt="QR Code">
    </div>
    
    <p>Or enter manually: <code>{{ secret }}</code></p>
    
    <form method="post" action="/admin/2fa/setup">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <div class="form-group">
            <label for="token">Enter 6-digit code:</label>
            <input type="text" id="token" name="token" maxlength="6" pattern="\d{6}" required>
        </div>
        <button type="submit" class="btn btn-primary">Enable 2FA</button>
    </form>
    
    <h3>Backup Codes</h3>
    <p>Save these codes in a safe place:</p>
    <code>{{ backup_codes|join('\n') }}</code>
</div>
{% endblock %}
```

## 10. Environment Variables Setup

Add to your `.env` file:

```
ENABLE_2FA=true
REQUIRE_2FA=false
MAX_LOGIN_ATTEMPTS=5
RATE_LIMIT_WINDOW=300
ACCOUNT_LOCKOUT_DURATION=900
SESSION_TIMEOUT=3600
```

## Testing

After implementation, test:

1. **Rate limiting**: Try logging in with wrong password 6 times
2. **2FA setup**: Enable 2FA and verify QR code works
3. **2FA login**: Test full login flow with TOTP
4. **Backup codes**: Test login with backup code
5. **Session timeout**: Wait 1 hour and verify automatic logout
6. **Security headers**: Use curl to verify headers: `curl -I https://yourdomain.com`

## Deployment Notes

- Set `SESSION_COOKIE_SECURE=true` in production (requires HTTPS)
- Use strong `SECRET_KEY` (min 32 random characters)
- Enable HTTPS with valid SSL certificate
- Regularly update dependencies
- Monitor failed login attempts
