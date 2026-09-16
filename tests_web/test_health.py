# -*- coding: utf-8 -*-
def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}

def test_healthz_db_down():
    from fastapi.testclient import TestClient
    from app.main import create_app

    class _BadDB:
        def execute(self, *a, **k):
            raise RuntimeError("db down")

    app = create_app()
    from app.db import get_db

    def _broken_db():
        yield _BadDB()

    app.dependency_overrides[get_db] = _broken_db
    with TestClient(app) as c:
        r = c.get("/healthz")
        assert r.status_code == 503
        assert r.json() == {"ok": False}


def test_product_page_public(client):
    """产品说明页无需登录即可访问并渲染表格。"""
    r = client.get("/product", follow_redirects=False)
    assert r.status_code == 200
    html = r.text
    assert "整体概念" in html
    assert "<table>" in html            # markdown 表格渲染
    assert "月度对账" in html
    raw = client.get("/product/raw")
    assert raw.status_code == 200 and "巡店数据结算系统" in raw.text
