from sqlalchemy import select
from sqlalchemy.orm import Session
from . import models, schemas

DEFAULT_SITE_SETTINGS = {
    "site_logo_url": "/static/brand/kaya-full-logo.svg",
    "header_logo_url": "/static/brand/kaya-icon.svg",
    "maintenance_enabled": "false",
    "maintenance_message": "Kaya is currently undergoing maintenance. Please check back shortly.",
}


# Admin user operations
def get_admin_by_email(db: Session, email: str):
    return db.scalar(select(models.AdminUser).where(models.AdminUser.email == email))


def get_or_create_admin(db: Session, email: str, password_hash: str):
    admin = get_admin_by_email(db, email)
    if not admin:
        admin = models.AdminUser(email=email, password_hash=password_hash)
        db.add(admin)
        db.commit()
        db.refresh(admin)
    return admin


def get_page_by_slug(db: Session, slug: str):
    return db.scalar(select(models.Page).where(models.Page.slug == models.normalize_slug(slug)))


def list_pages(db: Session, only_published: bool = True, navigation_only: bool = False):
    query = select(models.Page)
    if only_published:
        query = query.where(models.Page.published.is_(True))
    if navigation_only:
        query = query.where(models.Page.show_in_navigation.is_(True))
    query = query.order_by(models.Page.sort_order, models.Page.title)
    return db.scalars(query).all()


def create_page(db: Session, page_in: schemas.PageCreate):
    page = models.Page(**page_in.model_dump())
    db.add(page)
    db.commit()
    db.refresh(page)
    return page


def update_page(db: Session, page: models.Page, page_in: schemas.PageUpdate):
    for field, value in page_in.model_dump().items():
        setattr(page, field, value)
    db.add(page)
    db.commit()
    db.refresh(page)
    return page


def delete_page(db: Session, page: models.Page):
    db.delete(page)
    db.commit()


def get_post_by_slug(db: Session, slug: str):
    return db.scalar(select(models.Post).where(models.Post.slug == models.normalize_slug(slug)))


def list_posts(db: Session, only_published: bool = True):
    query = select(models.Post)
    if only_published:
        query = query.where(models.Post.published.is_(True))
    query = query.order_by(models.Post.published_at.desc(), models.Post.title)
    return db.scalars(query).all()


def create_post(db: Session, post_in: schemas.PostCreate):
    post = models.Post(**post_in.model_dump())
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


def update_post(db: Session, post: models.Post, post_in: schemas.PostUpdate):
    for field, value in post_in.model_dump().items():
        setattr(post, field, value)
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


def delete_post(db: Session, post: models.Post):
    db.delete(post)
    db.commit()


def create_upload(db: Session, filename: str, original_filename: str, content_type: str | None, size: int):
    upload = models.Upload(
        filename=filename,
        original_filename=original_filename,
        content_type=content_type,
        size=size,
    )
    db.add(upload)
    db.commit()
    db.refresh(upload)
    return upload


def list_uploads(db: Session):
    return db.scalars(select(models.Upload).order_by(models.Upload.created_at.desc())).all()


def set_site_setting(db: Session, key: str, value: str | None):
    setting = db.get(models.SiteSetting, key)
    if setting is None:
        setting = models.SiteSetting(key=key, value=value)
    else:
        setting.value = value
    db.add(setting)
    db.commit()
    db.refresh(setting)
    return setting


def get_site_settings(db: Session):
    values = dict(DEFAULT_SITE_SETTINGS)
    rows = db.scalars(select(models.SiteSetting)).all()
    for row in rows:
        values[row.key] = row.value or ""
    values["maintenance_enabled"] = str(values.get("maintenance_enabled", "false")).lower() == "true"
    return values


def seed_site_settings(db: Session):
    for key, value in DEFAULT_SITE_SETTINGS.items():
        if db.get(models.SiteSetting, key) is None:
            db.add(models.SiteSetting(key=key, value=value))
    db.commit()
