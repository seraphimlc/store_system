# -*- coding: utf-8 -*-
"""旧版业绩页已下线：/employees、/employees/{code} 302 到现行页面。
（/perf 已被 V3 绩效工资使用，不再重定向。）
"""
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.routers.auth_r import require_login

router = APIRouter()


def _denied():
    return RedirectResponse("/login", status_code=302)


@router.get("/employees", response_class=RedirectResponse)
def employees_list(request: Request,
                   user: Optional[User] = Depends(require_login),
                   db: Session = Depends(get_db)):
    if user is None:
        return _denied()
    return RedirectResponse("/staff-admin", status_code=302)


@router.get("/employees/{code}", response_class=RedirectResponse)
def employee_page(code: str, request: Request,
                  user: Optional[User] = Depends(require_login),
                  db: Session = Depends(get_db)):
    if user is None:
        return _denied()
    return RedirectResponse("/perf", status_code=302)
