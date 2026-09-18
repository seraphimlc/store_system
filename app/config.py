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
    # 奖额按月调整（形如 "2026-09=1250"；未列出的月份用 bonus_amount）
    bonus_amount_schedule: str = field(
        default_factory=lambda: os.environ.get(
            "BONUS_AMOUNT_SCHEDULE", "2026-09=1250"))
    # 按月生效的门槛调整（形如 "2026-09=75,2027-01=80"；未列出的月份用 bonus_group）
    # 例：2026-09 起门槛由 68 改为 75，8 月及以前保持 68（历史月份封账不受影响）
    # 解析默认口径（无规则说明 sheet 时套用；配置在 .env，非代码硬编码）
    #   形如 DEFAULT_VISIBLE_MAP="AUDIT_SUCCESS=candidate, AUDIT_FAILED=candidate, ~=blank"
    #        DEFAULT_POINT_RULES="OTHER|AUDIT_SUCCESS&YES=2, OTHER|AUDIT_SUCCESS&NO|~=1, ..."
    default_visible_map: str = field(
        default_factory=lambda: os.environ.get(
            "DEFAULT_VISIBLE_MAP",
            "AUDIT_SUCCESS=candidate, AUDIT_FAILED=candidate, "
            "OTHER=candidate, YES=candidate, NO=candidate, "
            "NOT_REQUEST=blank"))
    default_deploy_map: str = field(
        default_factory=lambda: os.environ.get("DEFAULT_DEPLOY_MAP", ""))
    default_point_rules: str = field(
        default_factory=lambda: os.environ.get(
            "DEFAULT_POINT_RULES",
            "*&YES=2, OTHER|AUDIT_SUCCESS|YES|NO&*=1"))
    bonus_group_schedule: str = field(
        default_factory=lambda: os.environ.get(
            "BONUS_GROUP_SCHEDULE", "2026-09=75"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
