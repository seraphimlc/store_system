"""Bounded, read-only map queries over the merchant source; no settlement data."""
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.services import bd_zone_operations as ops

SOURCE_TABLE = 'kanto_merchants_v20261029'
POINT_LIMIT = 1000
POINT_ZOOM = 17
MAX_LAT = 85.05112878
VISIT_EVENTS = "'complete_store_task','update_store_result'"


def _now(value=None):
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _mercator_y(lat):
    return (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2


def validate_view(west, south, east, north, zoom):
    if (any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in (west, south, east, north, zoom))
            or not (-180 <= west <= 180 and -180 <= east <= 180)
            or west == east or not -MAX_LAT <= south < north <= MAX_LAT
            or not 0 <= zoom <= 22):
        raise ops.OperationError('地图范围无效，请缩放后重试。')
    width = (east - west if east > west else east - west + 360) / 360
    height = _mercator_y(south) - _mercator_y(north)
    # At most 21×21 cells, even for a forged world-sized high-zoom request.
    bucket_zoom = max(0, min(int(zoom) + 1, 22, math.floor(math.log2(20 / max(width, height)))))
    return {'west': west, 'south': south, 'east': east, 'north': north,
            'scale': 2 ** bucket_zoom, 'bucket_zoom': bucket_zoom}


def _recent():
    return f'''WITH recent AS (
        SELECT store_source_id, MAX(created_at) AS last_visited_at
        FROM bd_task_event WHERE event_type IN ({VISIT_EVENTS})
          AND created_at >= :since AND created_at <= :now AND store_source_id<>''
        GROUP BY store_source_id
    )'''


def _bounds(db, params):
    if db.bind.dialect.name == 'postgresql':
        if params['west'] < params['east']:
            return 'point(m.lon,m.lat) <@ box(point(:west,:south),point(:east,:north))'
        return '''(point(m.lon,m.lat) <@ box(point(:west,:south),point(180,:north))
               OR point(m.lon,m.lat) <@ box(point(-180,:south),point(:east,:north)))'''
    # SQLite is used only by isolated tests; the source table must still exist.
    longitude = 'm.lon BETWEEN :west AND :east' if params['west'] < params['east'] else '(m.lon>=:west OR m.lon<=:east)'
    return f'm.lat BETWEEN :south AND :north AND {longitude}'


def _summary(count, covered):
    return {'merchant_count': count, 'covered_count': covered,
            'coverage_pct': round(covered * 100 / count, 1) if count else 0}


def viewport(db, user, *, west, south, east, north, zoom, now=None):
    ops.require_admin(user)
    params = validate_view(west, south, east, north, zoom)
    at = _now(now)
    params.update(now=at, since=at - timedelta(days=30), limit=POINT_LIMIT + 1)
    if db.bind.dialect.name == 'postgresql':
        db.execute(text("SET LOCAL statement_timeout='5s'"))
    base = f'''FROM {SOURCE_TABLE} m LEFT JOIN recent r ON r.store_source_id=m.place_id
               WHERE {_bounds(db, params)}'''
    if zoom >= POINT_ZOOM:
        rows = db.execute(text(f'''{_recent()}
            SELECT m.place_id,m.lat,m.lon,r.last_visited_at {base} LIMIT :limit'''), params).mappings().all()
        if len(rows) <= POINT_LIMIT:
            points = [{'id': r['place_id'], 'lat': r['lat'], 'lon': r['lon'],
                       'covered': r['last_visited_at'] is not None} for r in rows]
            return {'mode': 'points', 'points': points, 'cells': [], 'point_limit': POINT_LIMIT,
                    'summary': _summary(len(points), sum(p['covered'] for p in points)),
                    'dense': False, 'as_of': at.isoformat() + 'Z'}

    rows = db.execute(text(f'''{_recent()}
        SELECT FLOOR((m.lon+180)/360.0*:scale) AS bx,
               FLOOR((1-LN(TAN(RADIANS(m.lat))+1.0/COS(RADIANS(m.lat)))/PI())/2*:scale) AS by,
               AVG(m.lat) AS lat,AVG(m.lon) AS lon,COUNT(*) AS merchant_count,
               COUNT(r.store_source_id) AS covered_count {base} GROUP BY bx,by'''), params).mappings().all()
    cells = [{'id': f"{params['bucket_zoom']}:{int(r['bx'])}:{int(r['by'])}",
              'lat': float(r['lat']), 'lon': float(r['lon']),
              **_summary(int(r['merchant_count']), int(r['covered_count']))} for r in rows]
    return {'mode': 'clusters', 'cells': cells, 'points': [], 'point_limit': POINT_LIMIT,
            'summary': _summary(sum(c['merchant_count'] for c in cells), sum(c['covered_count'] for c in cells)),
            'dense': zoom >= POINT_ZOOM, 'as_of': at.isoformat() + 'Z'}


def merchant(db, user, identity, *, now=None):
    ops.require_admin(user)
    if not identity or len(identity) > 255:
        raise ops.OperationError('商户不存在')
    at = _now(now)
    row = db.execute(text(f'''SELECT m.place_id,m.name,m.address,m.lat,m.lon,m.phone,m.website,
        m.sources,m.prefecture,m.municipality,m.location_accuracy,
        (SELECT MAX(e.created_at) FROM bd_task_event e WHERE e.store_source_id=m.place_id
         AND e.event_type IN ({VISIT_EVENTS}) AND e.created_at<=:now) AS last_visited_at
        FROM {SOURCE_TABLE} m WHERE m.place_id=:identity'''), {'identity': identity, 'now': at}).mappings().first()
    if row is None:
        raise ops.OperationError('商户不存在')
    result = dict(row)
    last = result['last_visited_at']
    if isinstance(last, str):
        last = datetime.fromisoformat(last)
    result['covered'] = last is not None and last >= at - timedelta(days=30)
    result['last_visited_at'] = last.isoformat() + 'Z' if last else None
    return result
