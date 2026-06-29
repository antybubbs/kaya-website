from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.orm import validates
from .database import Base


def normalize_slug(value: str) -> str:
    return (value or "").strip().strip("/").lower().replace(" ", "-")


class Page(Base):
    __tablename__ = "pages"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    slug = Column(String(200), unique=True, nullable=False, index=True)
    meta_description = Column(String(300), nullable=True)
    content = Column(Text, nullable=True)
    published = Column(Boolean, default=False, nullable=False)
    show_in_navigation = Column(Boolean, default=False, nullable=False)
    sort_order = Column(Integer, default=100, nullable=False)

    @validates("slug")
    def validate_slug(self, key, value):
        return normalize_slug(value)


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    slug = Column(String(200), unique=True, nullable=False, index=True)
    excerpt = Column(String(400), nullable=True)
    content = Column(Text, nullable=True)
    published = Column(Boolean, default=False, nullable=False)
    published_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    @validates("slug")
    def validate_slug(self, key, value):
        return normalize_slug(value)


class Upload(Base):
    __tablename__ = "uploads"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=True)
    size = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SiteSetting(Base):
    __tablename__ = "site_settings"

    key = Column(String(100), primary_key=True, index=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
