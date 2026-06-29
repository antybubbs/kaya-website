from pydantic import BaseModel
from typing import Optional


class PageBase(BaseModel):
    title: str
    slug: str
    meta_description: Optional[str] = None
    content: Optional[str] = None
    published: bool = False
    show_in_navigation: bool = False
    sort_order: int = 100


class PageCreate(PageBase):
    pass


class PageUpdate(PageBase):
    pass


class PageOut(PageBase):
    id: int

    class Config:
        from_attributes = True


class PostBase(BaseModel):
    title: str
    slug: str
    excerpt: Optional[str] = None
    content: Optional[str] = None
    published: bool = False


class PostCreate(PostBase):
    pass


class PostUpdate(PostBase):
    pass
