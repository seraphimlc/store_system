"""Admin-only merchant distribution map."""
import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.routers.bd_zone_ops_r import error, render, work_login
from app.services import bd_merchant_map as maps, bd_zone_operations as ops

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get('/merchant-map')
def page(request: Request, user: User = Depends(work_login)):
    try:
        ops.require_admin(user)
    except ops.OperationError as exc:
        return error(exc)
    return render(request, user, 'bd_merchant_map.html')


@router.get('/merchant-map/data')
def data(west: float, south: float, east: float, north: float, zoom: float,
         user: User = Depends(work_login), db: Session = Depends(get_db)):
    try:
        result = maps.viewport(db, user, west=west, south=south, east=east, north=north, zoom=zoom)
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})
    except ops.OperationError as exc:
        return JSONResponse({'error': str(exc)}, status_code=403 if user.role != 'admin' else 422)
    except SQLAlchemyError:
        db.rollback()
        logger.exception('Merchant map viewport query failed')
        return JSONResponse({'error': '商户地图数据读取失败，请缩小范围后重试。'}, status_code=503)


@router.get('/merchant-map/detail')
def detail(identity: str = Query(..., min_length=1, max_length=255),
           user: User = Depends(work_login), db: Session = Depends(get_db)):
    try:
        return JSONResponse(maps.merchant(db, user, identity), headers={'Cache-Control': 'no-store'})
    except ops.OperationError as exc:
        return JSONResponse({'error': str(exc)}, status_code=403 if user.role != 'admin' else 404)
    except SQLAlchemyError:
        db.rollback()
        logger.exception('Merchant map detail query failed')
        return JSONResponse({'error': '商户详情读取失败，请重试。'}, status_code=503)
