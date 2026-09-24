# -*- coding: utf-8 -*-
"""公开产品说明页（无需登录，供客户查看）。"""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.templating import get_templates

import markdown as _md

router = APIRouter()
templates = get_templates()

_DOC = Path(__file__).resolve().parent.parent / "product_doc.md"


@router.get("/product", response_class=HTMLResponse)
def product_page(request: Request):
    text = _DOC.read_text(encoding="utf-8")
    body = _md.markdown(text, extensions=["tables", "fenced_code"])
    return templates.TemplateResponse("product.html", {
        "request": request, "body": body})


@router.get("/product/raw", response_class=HTMLResponse)
def product_raw(request: Request):
    """纯 Markdown 原文（方便复制/评审）。"""
    text = _DOC.read_text(encoding="utf-8")
    return HTMLResponse(f"<pre style='white-space:pre-wrap'>{text}</pre>")
