# -*- coding: utf-8 -*-
"""Web 测试共用 fixture：内存 sqlite（StaticPool 共享连接）+ TestClient。"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.db as appdb  # noqa: E402
from app.db import Base  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    appdb._ENGINE = engine
    appdb.SessionLocal = sessionmaker(bind=engine, future=True,
                                      expire_on_commit=False)
    Base.metadata.create_all(engine)
    app = create_app()
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(engine)
