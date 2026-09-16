# -*- coding: utf-8 -*-
"""Alembic 环境：URL 取自 app.config（环境变量 DATABASE_URL），元数据来自 app.models。"""
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings          # noqa: E402
from app.db import Base                      # noqa: E402
import app.models                           # noqa: E402,F401  注册全部表

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url():
    return get_settings().database_url


def run_migrations_offline():
    context.configure(url=get_url(), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = create_engine(get_url())
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
