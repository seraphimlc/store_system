# -*- coding: utf-8 -*-
"""Token 解析：前缀定位 + 摘要校验 + 撤销 + scope + bootstrap 只读（spec §5）。"""
import hashlib
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import ApiToken, Base, User
from mcp_service import tokens

RAW = "t" * 43


def _session(tmp_path, name="t.db"):
    eng = create_engine(f"sqlite:///{tmp_path}/{name}",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    return eng, S()


@pytest.fixture()
def db(tmp_path):
    eng, s = _session(tmp_path)
    u = User(username="admin", password_hash="x", role="admin", is_active=True)
    s.add(u)
    s.commit()
    s.add(ApiToken(user_id=u.id, name="test", token_prefix=RAW[:8],
                   token_hash=hashlib.sha256(RAW.encode()).hexdigest(),
                   scopes="read,write"))
    s.commit()
    yield s
    s.close()
    eng.dispose()


def test_resolve_ok(db):
    actor = tokens.resolve(db, RAW)
    assert actor is not None
    assert actor.role == "admin"
    assert "write" in actor.scopes
    assert actor.token_id is not None
    assert actor.can_write() is True


def test_unknown_token_rejected(db):
    assert tokens.resolve(db, "nope") is None


def test_wrong_secret_same_prefix_rejected(db):
    """前缀命中但摘要不符 → 拒绝（常量时间比较）。"""
    assert tokens.resolve(db, RAW[:8] + "x" * 35) is None


def test_revoked_token_rejected(db):
    row = db.query(ApiToken).first()
    row.revoked_at = datetime.utcnow()
    db.commit()
    assert tokens.resolve(db, RAW) is None


def test_inactive_user_rejected(db):
    u = db.query(User).first()
    u.is_active = False
    db.commit()
    assert tokens.resolve(db, RAW) is None


def test_last_used_updated(db):
    tokens.resolve(db, RAW)
    assert db.query(ApiToken).first().last_used_at is not None


def test_read_only_token_cannot_write(db):
    row = db.query(ApiToken).first()
    row.scopes = "read"
    db.commit()
    actor = tokens.resolve(db, RAW)
    assert actor.can_write() is False


def test_bootstrap_is_read_only(tmp_path):
    """api_tokens 为空时 env token 生效，且只给 read（spec §5.3）。"""
    eng, s = _session(tmp_path, "e.db")
    try:
        s.add(User(username="admin", password_hash="x", role="admin", is_active=True))
        s.commit()
        actor = tokens.resolve(s, "env-token-value", bootstrap_token="env-token-value")
        assert actor is not None
        assert actor.scopes == ["read"]
        assert actor.token_id is None
        assert actor.can_write() is False
    finally:
        s.close()
        eng.dispose()


def test_bootstrap_disabled_once_real_token_exists(db):
    """表内已有未撤销 Token → bootstrap 失效。"""
    assert tokens.resolve(db, "env-token-value",
                          bootstrap_token="env-token-value") is None


def test_issue_and_revoke_roundtrip(db):
    raw, row = tokens.issue(db, user_id=db.query(User).first().id,
                            name="cli", scopes="read,write")
    assert len(raw) >= 32
    assert tokens.resolve(db, raw) is not None
    assert tokens.revoke(db, row.id, row.user_id) is True
    assert tokens.resolve(db, raw) is None
    # 二次吊销返回 False（幂等语义）
    assert tokens.revoke(db, row.id, row.user_id) is False


def test_revoke_requires_ownership(db):
    _, row = tokens.issue(db, user_id=db.query(User).first().id, name="x")
    assert tokens.revoke(db, row.id, user_id=999) is False


def test_issue_rejects_bad_scopes(db):
    with pytest.raises(ValueError):
        tokens.issue(db, user_id=1, name="x", scopes="write")
