"""Retire station tasks and direct grid execution without deleting their history."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from app.routers.auth_r import require_login


def retired_station_path(path):
    return any(path == prefix or path.startswith(prefix + '/')
               for prefix in ('/tasks', '/stations', '/my/tasks', '/my/self-report'))


def retired_work_path(path):
    return any(path == prefix or path.startswith(prefix + '/')
               for prefix in ('/my/work/zones', '/my/work/grids'))


async def retired_page(request: Request, user=Depends(require_login)):
    if user is None:
        return RedirectResponse('/login', status_code=302)
    if request.method not in ('GET', 'HEAD'):
        return JSONResponse({'detail': 'legacy_task_retired'}, status_code=410)
    target = '/my/work'
    if user.role == 'admin':
        target = '/zones' if request.url.path.startswith('/stations') else '/zone-tasks'
    return RedirectResponse(target, status_code=303)


def include_current_routes(app, source):
    """Replace retired endpoints before registering, so old writes cannot execute."""
    current = APIRouter()
    for route in source.routes:
        if retired_station_path(route.path) or retired_work_path(route.path):
            current.add_api_route(route.path, retired_page, methods=sorted(route.methods),
                                  include_in_schema=False)
        else:
            current.routes.append(route)
    app.include_router(current)
