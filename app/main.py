from pathlib import Path
from uuid import uuid4
import re
import base64
import json
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import bleach
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown import markdown
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import crud, models, schemas
from .auth import (
    login_user,
    logout_user,
    require_admin,
    verify_password,
    get_password_hash,
    get_or_create_admin_user,
    setup_2fa,
    verify_2fa_setup,
    verify_2fa_token,
)
from .config import settings
from .database import SessionLocal, init_db
from .security import TOTP2FA, PasswordValidator

BASE_DIR = Path(__file__).resolve().parent
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml"}
GITHUB_API_BASE = "https://api.github.com/repos"
_version_cache = {}


env = Environment(
    loader=FileSystemLoader(str(BASE_DIR / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_nav_items(db):
    return crud.list_pages(db, only_published=True, navigation_only=True)


def has_admin_users(db) -> bool:
    return db.scalar(select(models.AdminUser.id)) is not None


def parse_github_repo(repo_url: str) -> tuple[str, str] | None:
    try:
        parsed = urlparse(repo_url)
        if parsed.netloc.lower() != "github.com":
            return None
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2:
            return None
        return parts[0], parts[1]
    except Exception:
        return None


def _read_json(url: str):
    req = Request(url, headers={"User-Agent": "kaya-website"})
    with urlopen(req, timeout=settings.github_api_timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_latest_repo_version(owner: str, repo: str) -> str | None:
    release_url = f"{GITHUB_API_BASE}/{owner}/{repo}/releases/latest"
    tags_url = f"{GITHUB_API_BASE}/{owner}/{repo}/tags?per_page=1"

    try:
        release = _read_json(release_url)
        tag = (release or {}).get("tag_name")
        if tag:
            return str(tag)
    except HTTPError as exc:
        if exc.code != 404:
            return None
    except (URLError, TimeoutError, ValueError):
        return None

    try:
        tags = _read_json(tags_url)
        if isinstance(tags, list) and tags:
            name = tags[0].get("name")
            if name:
                return str(name)
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None
    return None


def get_release_versions() -> dict[str, str]:
    repos = {
        "website": parse_github_repo(settings.website_github_url),
        "app": parse_github_repo(settings.github_url),
    }
    now = time.time()
    versions = {
        "website": settings.kaya_version,
        "app": settings.kaya_version,
    }

    for key, repo in repos.items():
        if not repo:
            continue
        owner, name = repo
        cache_key = f"{owner}/{name}"
        cache_entry = _version_cache.get(cache_key)
        if cache_entry and now - cache_entry["ts"] < settings.github_version_cache_seconds:
            versions[key] = cache_entry["version"]
            continue

        latest = fetch_latest_repo_version(owner, name)
        if latest:
            _version_cache[cache_key] = {"version": latest, "ts": now}
            versions[key] = latest
        elif cache_entry:
            versions[key] = cache_entry["version"]

    return versions


def common_context(db=None, **context):
    if db is not None:
        context.setdefault("nav_items", get_nav_items(db))
        context.setdefault("site_config", crud.get_site_settings(db))
    context.setdefault("settings", settings)
    context.setdefault("release_versions", get_release_versions())
    return context


def render_template(template_name: str, **context):
    template = env.get_template(template_name)
    return HTMLResponse(template.render(**context))


def sanitize_html(content: str) -> str:
    allowed_tags = bleach.sanitizer.ALLOWED_TAGS | {
        "p", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "code", "img", "table", "thead",
        "tbody", "tr", "th", "td", "ul", "ol", "li", "strong", "em", "blockquote", "hr", "br"
    }
    allowed_attrs = {
        "a": ["href", "title", "rel", "target"],
        "img": ["src", "alt", "title"],
        "code": ["class"],
        "th": ["align"],
        "td": ["align"],
    }
    raw_html = markdown(content or "", extensions=["fenced_code", "tables", "sane_lists"])
    return bleach.clean(raw_html, tags=allowed_tags, attributes=allowed_attrs, protocols=["http", "https", "mailto"], strip=True)


def page_payload(title, slug, meta_description, content, order, nav=True):
    return {
        "title": title,
        "slug": slug,
        "meta_description": meta_description,
        "content": content.strip(),
        "published": True,
        "show_in_navigation": nav,
        "sort_order": order,
    }


def seed_default_pages(db):
    if db.scalar(select(models.Page)):
        return

    pages = [
        page_payload(
            "Home", "",
            "Kaya is a self-hosted infrastructure management platform for servers, services, assets, remote access, runbooks, licences and operational knowledge.",
            "", 0, False,
        ),
        page_payload(
            "Features", "features",
            "Explore Kaya features for inventory, remote access, runbooks, licences, Docker and VM monitoring, and audit trails.",
            """
## Kaya feature map

Kaya keeps the practical operational facts of a small estate in one private place: assets, servers, services, remote access paths, licences, runbooks and audits.

| Area | What Kaya helps you track |
| --- | --- |
| Infrastructure dashboard | Health, ownership, service status and recent changes |
| Server and service inventory | Physical hosts, VMs, Docker workloads and service metadata |
| Remote Manager | RDP, SSH and operational access details with a single launch surface |
| Runbooks | Markdown procedures attached to the systems that need them |
| Licence management | Keys, renewal dates, vendors and compliance notes |
| Audit logs | A timeline of day-to-day operational changes |
""", 10,
        ),
        page_payload(
            "Screenshots", "screenshots",
            "Preview the Kaya dashboard, inventory, runbooks and remote management workflows.",
            """
## Product screenshots

Upload real Kaya screenshots in the admin media library, then embed them in this page with Markdown:

```markdown
![Kaya dashboard](/uploads/your-screenshot.png)
```

The seeded homepage includes a polished dashboard mockup so the public site feels complete before real screenshots are added.
""", 20,
        ),
        page_payload(
            "Demo", "demo",
            "Try the Kaya demo or follow the project on GitHub.",
            """
## Demo

A public demo link can be configured with `DEMO_URL`. Until a hosted demo is available, use the GitHub repository to run Kaya locally and explore the product in your own environment.

[Open Kaya on GitHub](https://github.com/antybubbs/kaya)
""", 30,
        ),
        page_payload(
            "Install", "install",
            "Install Kaya with Docker Compose for private self-hosted operations management.",
            """
## Install Kaya

Kaya is designed for Docker-first deployment on your own host.

```bash
git clone https://github.com/antybubbs/kaya.git
cd kaya
cp .env.example .env
docker compose up -d --build
```

Keep your `.env`, database and uploaded assets backed up before updates.
""", 40,
        ),
        page_payload(
            "Documentation", "documentation",
            "Read Kaya documentation, install notes and operational guidance.",
            """
## Documentation

Use this page as the public documentation hub for Kaya. Add install guides, screenshots, release notes, reverse proxy examples and upgrade notes from the admin editor.

Useful starting points:

- Quick install
- Backup and restore
- Remote Manager setup
- Docker and VM monitoring
- Runbook conventions
""", 50,
        ),
        page_payload(
            "Roadmap", "roadmap",
            "Roadmap for Kaya self-hosted infrastructure management features.",
            """
## Roadmap

Kaya is moving toward a deeper infrastructure operations console for homelabs and small teams.

- Richer topology and dependency mapping
- Improved remote access flows
- More detailed Docker and VM telemetry
- Better release and update workflows
- Additional import and export options
""", 60,
        ),
        page_payload(
            "About Kaya", "about-kaya",
            "Learn more about Kaya, the self-hosted infrastructure platform for homelabs and small teams.",
            """
## About Kaya

Kaya is built for people who run real infrastructure without wanting a heavyweight enterprise platform. It gives homelabs and small infrastructure teams a calm, private place to manage services, assets, runbooks, licences and daily operational knowledge.
""", 70,
        ),
        page_payload(
            "Contact", "contact",
            "Contact Kaya and find the project on GitHub.",
            """
## Contact

Kaya is developed in the open on GitHub.

[GitHub repository](https://github.com/antybubbs/kaya)
""", 80,
        ),
    ]

    for item in pages:
        crud.create_page(db, schemas.PageCreate(**item))

    crud.create_post(db, schemas.PostCreate(
        title="Kaya website is ready for self-hosted publishing",
        slug="kaya-website-self-hosted-publishing",
        excerpt="A seeded update post for the standalone Kaya marketing site, with editable Markdown content and persistent uploads.",
        content="""
## A public home for Kaya

This website is a separate Docker-hosted project for presenting Kaya to users, contributors and self-hosting operators. It includes editable pages, Markdown rendering, image uploads and a simple admin area backed by SQLite.

Use the admin editor to turn this seeded post into your first real update.
""".strip(),
        published=True,
    ))


async def save_upload(upload: UploadFile, db):
    if not upload.filename:
        return None
    if upload.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only image uploads are supported.")
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", upload.filename).strip(".-") or "upload"
    unique_name = f"{uuid4().hex}-{safe_name}"
    upload_path = settings.uploads_dir / unique_name
    content = await upload.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Uploads must be 8MB or smaller.")
    upload_path.write_bytes(content)
    return crud.create_upload(db, unique_name, upload.filename, upload.content_type, len(content))


def build_page_form(title, slug, meta_description, content, published, show_in_navigation, sort_order):
    return schemas.PageCreate(
        title=title,
        slug=slug,
        meta_description=meta_description,
        content=content,
        published=published,
        show_in_navigation=show_in_navigation,
        sort_order=sort_order,
    )


def build_post_form(title, slug, excerpt, content, published):
    return schemas.PostCreate(title=title, slug=slug, excerpt=excerpt, content=content, published=published)


def create_app():
    app = FastAPI(title="Kaya Website")
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        https_only=settings.session_cookie_secure,
        same_site="lax",
    )
    if settings.allowed_hosts and settings.allowed_hosts != "*":
        hosts = [host.strip() for host in settings.allowed_hosts.split(",") if host.strip()]
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    app.mount("/uploads", StaticFiles(directory=settings.uploads_dir), name="uploads")

    @app.middleware("http")
    async def maintenance_mode_middleware(request: Request, call_next):
        path = request.url.path
        if path.startswith(("/admin", "/static", "/uploads")):
            return await call_next(request)
        with SessionLocal() as db:
            site_config = crud.get_site_settings(db)
            if site_config.get("maintenance_enabled"):
                return render_template(
                    "maintenance.html",
                    title="Maintenance",
                    nav_items=[],
                    site_config=site_config,
                    settings=settings,
                )
        return await call_next(request)

    @app.on_event("startup")
    def startup_event():
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        Path(settings.database_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
        init_db()
        with SessionLocal() as db:
            crud.seed_site_settings(db)
            seed_default_pages(db)

    @app.get("/", response_class=HTMLResponse)
    def public_home(request: Request, db=Depends(get_db)):
        posts = crud.list_posts(db, only_published=True)[:3]
        return render_template("home.html", **common_context(db, title="Kaya", posts=posts))

    @app.get("/blog", response_class=HTMLResponse)
    def public_blog(request: Request, db=Depends(get_db)):
        posts = crud.list_posts(db, only_published=True)
        return render_template("blog.html", **common_context(db, title="Kaya updates", posts=posts, meta_description="Kaya project updates, releases and self-hosted infrastructure notes."))

    @app.get("/blog/{slug}", response_class=HTMLResponse)
    def public_post(request: Request, slug: str, db=Depends(get_db)):
        post = crud.get_post_by_slug(db, slug)
        if not post or not post.published:
            raise HTTPException(status_code=404, detail="Post not found")
        return render_template(
            "post.html",
            **common_context(db, title=post.title, meta_description=post.excerpt, post=post, post_html=sanitize_html(post.content or "")),
        )

    @app.get("/admin", response_class=HTMLResponse)
    def admin_login(request: Request, db=Depends(get_db)):
        if not has_admin_users(db):
            return RedirectResponse(url="/admin/setup", status_code=status.HTTP_302_FOUND)
        if request.session.get("admin_authenticated"):
            return RedirectResponse(url="/admin/pages", status_code=status.HTTP_302_FOUND)
        return render_template("admin_login.html", title="Admin login", nav_items=[], error=None, settings=settings)

    @app.get("/admin/setup", response_class=HTMLResponse)
    def admin_setup(request: Request, db=Depends(get_db)):
        if has_admin_users(db):
            return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
        return render_template(
            "admin_setup.html",
            title="Admin setup",
            nav_items=[],
            error=None,
            message="Create the first admin account to finish setup.",
            settings=settings,
        )

    @app.post("/admin/setup")
    async def admin_setup_post(
        request: Request,
        email: str = Form(...),
        password: str = Form(...),
        confirm_password: str = Form(...),
        db=Depends(get_db),
    ):
        if has_admin_users(db):
            return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)

        if password != confirm_password:
            return render_template(
                "admin_setup.html",
                title="Admin setup",
                nav_items=[],
                error="Passwords do not match",
                message=None,
                settings=settings,
            )

        valid, validation_error = PasswordValidator.validate(password)
        if not valid:
            return render_template(
                "admin_setup.html",
                title="Admin setup",
                nav_items=[],
                error=validation_error,
                message=None,
                settings=settings,
            )

        admin = models.AdminUser(
            email=email.strip().lower(),
            password_hash=get_password_hash(password),
        )
        db.add(admin)
        try:
            db.commit()
            db.refresh(admin)
        except IntegrityError:
            db.rollback()
            return render_template(
                "admin_setup.html",
                title="Admin setup",
                nav_items=[],
                error="That email is already in use.",
                message=None,
                settings=settings,
            )

        login_user(request, admin.email)
        return RedirectResponse(url="/admin/pages", status_code=status.HTTP_302_FOUND)

    @app.post("/admin/login")
    async def admin_login_post(request: Request, email: str = Form(...), password: str = Form(...), db=Depends(get_db)):
        if not has_admin_users(db):
            return RedirectResponse(url="/admin/setup", status_code=status.HTTP_302_FOUND)

        admin = crud.get_admin_by_email(db, email)
        if admin and verify_password(password, admin.password_hash):
            login_user(request, email)
            return RedirectResponse(url="/admin/pages", status_code=status.HTTP_302_FOUND)

        return render_template("admin_login.html", title="Admin login", nav_items=[], error="Invalid credentials", settings=settings)

    @app.get("/admin/logout")
    def admin_logout(request: Request):
        logout_user(request)
        return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)

    @app.get("/admin/pages", response_class=HTMLResponse)
    def admin_pages(request: Request, db=Depends(get_db)):
        require_admin(request)
        pages = crud.list_pages(db, only_published=False)
        return render_template("admin_pages.html", title="Pages", pages=pages, nav_items=[], settings=settings)

    @app.get("/admin/pages/new", response_class=HTMLResponse)
    def admin_new_page(request: Request, db=Depends(get_db)):
        require_admin(request)
        return render_template("admin_edit_page.html", title="Create page", page=None, uploads=crud.list_uploads(db), form_action="/admin/pages/new", nav_items=[], message=None, settings=settings)

    @app.post("/admin/pages/new")
    async def admin_create_page(request: Request, title: str = Form(...), slug: str = Form(""), meta_description: str = Form(""), content: str = Form(""), published: bool = Form(False), show_in_navigation: bool = Form(False), sort_order: int = Form(100), image: UploadFile | None = File(None), db=Depends(get_db)):
        require_admin(request)
        page_in = build_page_form(title, slug, meta_description, content, published, show_in_navigation, sort_order)
        try:
            crud.create_page(db, page_in)
            if image and image.filename:
                await save_upload(image, db)
        except IntegrityError:
            db.rollback()
            return render_template("admin_edit_page.html", title="Create page", page=None, uploads=crud.list_uploads(db), form_action="/admin/pages/new", nav_items=[], message="A page with that slug already exists.", settings=settings)
        return RedirectResponse(url="/admin/pages", status_code=status.HTTP_302_FOUND)

    @app.get("/admin/pages/{page_id}/edit", response_class=HTMLResponse)
    def admin_edit_page(request: Request, page_id: int, db=Depends(get_db)):
        require_admin(request)
        page = db.get(models.Page, page_id)
        if not page:
            raise HTTPException(status_code=404, detail="Not found")
        return render_template("admin_edit_page.html", title="Edit page", page=page, uploads=crud.list_uploads(db), form_action=f"/admin/pages/{page_id}/edit", nav_items=[], message=None, settings=settings)

    @app.post("/admin/pages/{page_id}/edit")
    async def admin_update_page(request: Request, page_id: int, title: str = Form(...), slug: str = Form(""), meta_description: str = Form(""), content: str = Form(""), published: bool = Form(False), show_in_navigation: bool = Form(False), sort_order: int = Form(100), image: UploadFile | None = File(None), db=Depends(get_db)):
        require_admin(request)
        page = db.get(models.Page, page_id)
        if not page:
            raise HTTPException(status_code=404, detail="Not found")
        page_in = schemas.PageUpdate(**build_page_form(title, slug, meta_description, content, published, show_in_navigation, sort_order).model_dump())
        try:
            crud.update_page(db, page, page_in)
            if image and image.filename:
                await save_upload(image, db)
        except IntegrityError:
            db.rollback()
            return render_template("admin_edit_page.html", title="Edit page", page=page, uploads=crud.list_uploads(db), form_action=f"/admin/pages/{page_id}/edit", nav_items=[], message="A page with that slug already exists.", settings=settings)
        return RedirectResponse(url="/admin/pages", status_code=status.HTTP_302_FOUND)

    @app.post("/admin/pages/{page_id}/delete")
    def admin_delete_page(request: Request, page_id: int, db=Depends(get_db)):
        require_admin(request)
        page = db.get(models.Page, page_id)
        if not page:
            raise HTTPException(status_code=404, detail="Not found")
        crud.delete_page(db, page)
        return RedirectResponse(url="/admin/pages", status_code=status.HTTP_302_FOUND)

    @app.get("/admin/posts", response_class=HTMLResponse)
    def admin_posts(request: Request, db=Depends(get_db)):
        require_admin(request)
        return render_template("admin_posts.html", title="Updates", posts=crud.list_posts(db, only_published=False), nav_items=[], settings=settings)

    @app.get("/admin/posts/new", response_class=HTMLResponse)
    def admin_new_post(request: Request, db=Depends(get_db)):
        require_admin(request)
        return render_template("admin_edit_post.html", title="Create update", post=None, uploads=crud.list_uploads(db), form_action="/admin/posts/new", nav_items=[], message=None, settings=settings)

    @app.post("/admin/posts/new")
    async def admin_create_post(request: Request, title: str = Form(...), slug: str = Form(...), excerpt: str = Form(""), content: str = Form(""), published: bool = Form(False), image: UploadFile | None = File(None), db=Depends(get_db)):
        require_admin(request)
        try:
            crud.create_post(db, build_post_form(title, slug, excerpt, content, published))
            if image and image.filename:
                await save_upload(image, db)
        except IntegrityError:
            db.rollback()
            return render_template("admin_edit_post.html", title="Create update", post=None, uploads=crud.list_uploads(db), form_action="/admin/posts/new", nav_items=[], message="A post with that slug already exists.", settings=settings)
        return RedirectResponse(url="/admin/posts", status_code=status.HTTP_302_FOUND)

    @app.get("/admin/posts/{post_id}/edit", response_class=HTMLResponse)
    def admin_edit_post(request: Request, post_id: int, db=Depends(get_db)):
        require_admin(request)
        post = db.get(models.Post, post_id)
        if not post:
            raise HTTPException(status_code=404, detail="Not found")
        return render_template("admin_edit_post.html", title="Edit update", post=post, uploads=crud.list_uploads(db), form_action=f"/admin/posts/{post_id}/edit", nav_items=[], message=None, settings=settings)

    @app.post("/admin/posts/{post_id}/edit")
    async def admin_update_post(request: Request, post_id: int, title: str = Form(...), slug: str = Form(...), excerpt: str = Form(""), content: str = Form(""), published: bool = Form(False), image: UploadFile | None = File(None), db=Depends(get_db)):
        require_admin(request)
        post = db.get(models.Post, post_id)
        if not post:
            raise HTTPException(status_code=404, detail="Not found")
        try:
            crud.update_post(db, post, schemas.PostUpdate(**build_post_form(title, slug, excerpt, content, published).model_dump()))
            if image and image.filename:
                await save_upload(image, db)
        except IntegrityError:
            db.rollback()
            return render_template("admin_edit_post.html", title="Edit update", post=post, uploads=crud.list_uploads(db), form_action=f"/admin/posts/{post_id}/edit", nav_items=[], message="A post with that slug already exists.", settings=settings)
        return RedirectResponse(url="/admin/posts", status_code=status.HTTP_302_FOUND)

    @app.post("/admin/posts/{post_id}/delete")
    def admin_delete_post(request: Request, post_id: int, db=Depends(get_db)):
        require_admin(request)
        post = db.get(models.Post, post_id)
        if not post:
            raise HTTPException(status_code=404, detail="Not found")
        crud.delete_post(db, post)
        return RedirectResponse(url="/admin/posts", status_code=status.HTTP_302_FOUND)

    @app.get("/admin/uploads", response_class=HTMLResponse)
    def admin_uploads(request: Request, db=Depends(get_db)):
        require_admin(request)
        return render_template("admin_uploads.html", title="Uploads", uploads=crud.list_uploads(db), nav_items=[], message=None, settings=settings)

    @app.post("/admin/uploads", response_class=HTMLResponse)
    async def admin_upload_create(request: Request, image: UploadFile = File(...), db=Depends(get_db)):
        require_admin(request)
        await save_upload(image, db)
        return RedirectResponse(url="/admin/uploads", status_code=status.HTTP_302_FOUND)

    @app.get("/admin/settings", response_class=HTMLResponse)
    def admin_site_settings(request: Request, db=Depends(get_db)):
        require_admin(request)
        return render_template(
            "admin_settings.html",
            title="Site settings",
            nav_items=[],
            site_config=crud.get_site_settings(db),
            uploads=crud.list_uploads(db),
            message=None,
            settings=settings,
        )

    @app.post("/admin/settings", response_class=HTMLResponse)
    async def admin_site_settings_update(
        request: Request,
        site_logo_url: str = Form(""),
        header_logo_url: str = Form(""),
        home_hero_image_url: str = Form(""),
        maintenance_enabled: bool = Form(False),
        maintenance_message: str = Form(""),
        home_content: str = Form(""),
        logo_image: UploadFile | None = File(None),
        header_logo_image: UploadFile | None = File(None),
        home_hero_image: UploadFile | None = File(None),
        db=Depends(get_db),
    ):
        require_admin(request)
        if logo_image and logo_image.filename:
            uploaded_logo = await save_upload(logo_image, db)
            site_logo_url = f"/uploads/{uploaded_logo.filename}"
        if header_logo_image and header_logo_image.filename:
            uploaded_header_logo = await save_upload(header_logo_image, db)
            header_logo_url = f"/uploads/{uploaded_header_logo.filename}"
        if home_hero_image and home_hero_image.filename:
            uploaded_home_hero = await save_upload(home_hero_image, db)
            home_hero_image_url = f"/uploads/{uploaded_home_hero.filename}"
        crud.set_site_setting(db, "site_logo_url", site_logo_url or "/static/brand/kaya-full-logo.svg")
        crud.set_site_setting(db, "header_logo_url", header_logo_url or "/static/brand/kaya-full-logo.svg")
        crud.set_site_setting(db, "home_hero_image_url", home_hero_image_url or "/static/kaya-dashboard-screenshot.svg")
        crud.set_site_setting(db, "maintenance_enabled", "true" if maintenance_enabled else "false")
        crud.set_site_setting(db, "maintenance_message", maintenance_message or "Kaya is currently undergoing maintenance. Please check back shortly.")
        crud.set_site_setting(db, "home_content", home_content or "<h2>Welcome</h2><p>Edit this content in Settings.</p>")
        return render_template(
            "admin_settings.html",
            title="Site settings",
            nav_items=[],
            site_config=crud.get_site_settings(db),
            uploads=crud.list_uploads(db),
            message="Settings saved.",
            settings=settings,
        )

    @app.get("/admin/user-settings", response_class=HTMLResponse)
    def admin_user_settings(request: Request, db=Depends(get_db)):
        require_admin(request)
        
        admin = get_or_create_admin_user(db)
        if not admin:
            return RedirectResponse(url="/admin/setup", status_code=status.HTTP_302_FOUND)
        
        return render_template(
            "admin_user_settings.html",
            title="User Settings",
            nav_items=[],
            two_fa_enabled=admin.totp_enabled if admin else False,
            show_2fa_setup=False,
            message=None,
            error=None,
            settings=settings,
        )

    @app.post("/admin/user-settings", response_class=HTMLResponse)
    async def admin_user_settings_update(
        request: Request,
        section: str = Form(...),
        current_password: str = Form(""),
        new_password: str = Form(""),
        confirm_password: str = Form(""),
        password: str = Form(""),
        token: str = Form(""),
        db=Depends(get_db),
    ):
        require_admin(request)

        admin = get_or_create_admin_user(db)
        if not admin:
            return RedirectResponse(url="/admin/setup", status_code=status.HTTP_302_FOUND)
        
        message = None
        error = None
        show_2fa_setup = False
        qr_code = None
        totp_secret = None
        backup_codes = None

        if section == "password":
            # Verify current password
            if not verify_password(current_password, admin.password_hash):
                error = "Current password is incorrect"
            elif new_password != confirm_password:
                error = "Passwords do not match"
            elif not new_password:
                error = "New password cannot be empty"
            else:
                # Validate password strength
                is_valid, error_msg = PasswordValidator.validate(new_password)
                if not is_valid:
                    error = error_msg
                else:
                    # Update password
                    admin.password_hash = get_password_hash(new_password)
                    db.commit()
                    message = "Password updated successfully"

        elif section == "setup-2fa":
            if admin.totp_enabled:
                error = "2FA is already enabled"
            else:
                # Generate new secret
                provisioning_uri, codes = setup_2fa(db, admin)
                qr_code_bytes = TOTP2FA.get_qr_code(provisioning_uri)
                qr_code = base64.b64encode(qr_code_bytes).decode()
                totp_secret = admin.totp_secret
                backup_codes = codes
                show_2fa_setup = True
                request.session["2fa_setup_in_progress"] = True

        elif section == "verify-2fa":
            if not request.session.get("2fa_setup_in_progress"):
                error = "2FA setup not initiated"
            elif not admin.totp_secret:
                error = "2FA secret not found"
            elif not token:
                error = "Please enter a 6-digit code"
            elif not verify_2fa_setup(db, admin, token):
                error = "Invalid code. Please try again"
            else:
                request.session.pop("2fa_setup_in_progress", None)
                message = "Two-Factor Authentication enabled successfully!"
                # Re-fetch admin to get updated 2FA status
                db.refresh(admin)

        elif section == "disable-2fa":
            if not verify_password(password, admin.password_hash):
                error = "Password is incorrect"
            elif not admin.totp_enabled:
                error = "2FA is not currently enabled"
            else:
                admin.totp_enabled = False
                admin.totp_secret = None
                admin.backup_codes = None
                db.commit()
                message = "Two-Factor Authentication disabled"

        return render_template(
            "admin_user_settings.html",
            title="User Settings",
            nav_items=[],
            two_fa_enabled=admin.totp_enabled if admin else False,
            show_2fa_setup=show_2fa_setup,
            qr_code=qr_code,
            totp_secret=totp_secret,
            backup_codes=backup_codes,
            message=message,
            error=error,
            settings=settings,
        )

    @app.get("/{slug:path}", response_class=HTMLResponse)
    def public_page(request: Request, slug: str, db=Depends(get_db)):
        clean_slug = models.normalize_slug(slug)
        page = crud.get_page_by_slug(db, clean_slug)
        if not page or not page.published:
            raise HTTPException(status_code=404, detail="Page not found")
        return render_template("page.html", **common_context(db, title=page.title, meta_description=page.meta_description, page=page, page_html=sanitize_html(page.content or "")))

    return app


app = create_app()


