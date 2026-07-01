from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base
from .config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
Base = declarative_base()


def init_db():
    from .models import Page, Post, Upload, SiteSetting

    Base.metadata.create_all(bind=engine)

    # Migrate existing databases that predate the parent_id column
    inspector = inspect(engine)
    if "pages" in inspector.get_table_names():
        existing_columns = {col["name"] for col in inspector.get_columns("pages")}
        if "parent_id" not in existing_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE pages ADD COLUMN parent_id INTEGER REFERENCES pages(id)"))
