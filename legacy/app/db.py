# -*- coding: utf-8 -*-
"""SQLAlchemy engine/session；环境变量 DATABASE_URL 控制（测试用 sqlite://）。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    url = get_settings().database_url
    kwargs = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


def get_engine():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = _make_engine()
    return _ENGINE


_ENGINE = None


def reset_engine_for_tests(url="sqlite://"):
    """测试用：重建内存库引擎（Base.metadata.create_all 由 conftest 调用）。"""
    global _ENGINE
    _ENGINE = create_engine(url, future=True, connect_args={
        "check_same_thread": False}, poolclass=None) \
        if url.startswith("sqlite") else create_engine(url, future=True)
    return _ENGINE


def make_session_factory():
    return sessionmaker(bind=get_engine(), future=True, expire_on_commit=False)


SessionLocal = make_session_factory()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
