# -*- coding: utf-8 -*-
"""Web 应用配置：从环境变量读取（default_factory 保证每次构造读最新 env），
默认适合本地开发。"""
import os
from dataclasses import dataclass, field
from functools import lru_cache


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "DATABASE_URL", "sqlite:///./store_settle.db"))
    secret_key: str = field(
        default_factory=lambda: os.environ.get(
            "SECRET_KEY", "dev-secret-change-me"))
    session_ttl_hours: int = field(
        default_factory=lambda: _env_int("SESSION_TTL_HOURS", 12))
    upload_dir: str = field(
        default_factory=lambda: os.environ.get(
            "UPLOAD_DIR", "./data/uploads"))
    max_upload_mb: int = field(
        default_factory=lambda: _env_int("MAX_UPLOAD_MB", 50))
    admin_username: str = field(
        default_factory=lambda: os.environ.get("ADMIN_USERNAME", "admin"))
    admin_password: str = field(
        default_factory=lambda: os.environ.get("ADMIN_PASSWORD", ""))
    default_staff_password: str = field(
        default_factory=lambda: os.environ.get(
            "DEFAULT_STAFF_PASSWORD", "demo123"))
    ai_api_key: str = field(
        default_factory=lambda: os.environ.get("AI_API_KEY", ""))
    ai_base_url: str = field(
        default_factory=lambda: os.environ.get(
            "AI_BASE_URL", "https://api.openai.com/v1"))
    ai_model: str = field(
        default_factory=lambda: os.environ.get("AI_MODEL", "gpt-4o-mini"))
    # 奖金规则（可配）：每满 bonus_group 点奖 bonus_amount 円（整月滚动、不跨月）
    bonus_group: int = field(default_factory=lambda: _env_int("BONUS_GROUP", 68))
    bonus_amount: int = field(
        default_factory=lambda: _env_int("BONUS_AMOUNT", 3000))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
