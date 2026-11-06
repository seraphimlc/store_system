"""Pick sample points inside a grid and keep Places pins that fall in it.

HTTP stays in ``scripts/bd_fetch_grid_places.py``. A pin is kept when it lies
inside the grid or within ``BOUNDARY_M`` of the boundary, so a station entrance
on the line still counts. The geometric centroid is not used: on a concave
grid it can fall outside the polygon.
"""
from __future__ import annotations

from collections import Counter
import math
import re

from shapely.geometry import Point
from shapely.ops import transform, unary_union

from app.services.bd_zones import _TO_LONLAT, _TO_METERS

STATION_TYPES = ('train_station', 'subway_station', 'light_rail_station')
LANDMARK_TYPES = ('shopping_mall', 'department_store', 'tourist_attraction',
                  'museum', 'university', 'stadium', 'amusement_park')
STATION_RADIUS_M = 1800.0
LANDMARK_RADIUS_M = 1600.0
BOUNDARY_M = 30.0
MIN_RADIUS_M = 250.0
MAX_RADIUS_M = 50000.0


def to_metric(geom):
    return transform(_TO_METERS, geom)


def to_lonlat(geom):
    return transform(_TO_LONLAT, geom)


def cover_radius(poly_m):
    point = poly_m.representative_point()
    return point, float(poly_m.hausdorff_distance(point))


def _pull_inside(poly_m, x, y, spacing):
    candidate = Point(x, y)
    if poly_m.covers(candidate):
        return candidate
    if poly_m.distance(candidate) > spacing * 0.35:
        return None
    interior = poly_m.representative_point()
    for step in (0.25, 0.5, 0.75, 1):
        trial = Point(x + (interior.x - x) * step, y + (interior.y - y) * step)
        if poly_m.covers(trial):
            return trial
    return None


def _polygonal_parts(geom):
    if geom.is_empty:
        return []
    if geom.geom_type == 'Polygon':
        return [geom]
    if geom.geom_type in ('MultiPolygon', 'GeometryCollection'):
        parts = []
        for part in geom.geoms:
            parts.extend(_polygonal_parts(part))
        return parts
    return []


def sample_points(poly_m, radius_m, *, limit=40):
    """Interior points whose circles of ``radius_m`` cover the grid."""
    origin, far = cover_radius(poly_m)
    if far <= radius_m or poly_m.area <= 0:
        return [origin]
    spacing = radius_m * math.sqrt(2) * 0.95
    minx, miny, maxx, maxy = poly_m.bounds
    points = [origin]
    y = miny
    row = 0
    while y <= maxy and len(points) < limit:
        x = minx + (0 if row % 2 == 0 else spacing / 2)
        while x <= maxx and len(points) < limit:
            candidate = _pull_inside(poly_m, x, y, spacing)
            if candidate is not None and all(candidate.distance(kept) > spacing * 0.45 for kept in points):
                points.append(candidate)
            x += spacing
        y += spacing * 0.866
        row += 1
    covered = unary_union([point.buffer(radius_m) for point in points])
    rest = poly_m.difference(covered)
    for part in _polygonal_parts(rest):
        if len(points) >= limit or part.area < 20_000:
            break
        extra = part.representative_point()
        if poly_m.covers(extra) and all(extra.distance(kept) > 100 for kept in points):
            points.append(extra)
    return points


def circle_radius(poly_m, radius_m):
    """Use a tighter circle when one sample already covers the grid."""
    _point, far = cover_radius(poly_m)
    if far <= radius_m:
        return min(MAX_RADIUS_M, max(MIN_RADIUS_M, far * 1.08))
    return radius_m


def japanese_name(name, language):
    text = (name or '').strip()
    if not text or len(text) > 80:
        return None
    if language and language != 'ja':
        return None
    if not language and not any('\u3040' <= char <= '\u30ff' or '\u4e00' <= char <= '\u9fff' for char in text):
        return None
    return text


OFFICE_MARKS = ('事務', '課', '係', '担当', '学籍', '入試', 'キャリア', '庶務', '証明', '史料館', '資料館')
MONUMENT_ENDS = ('記念碑', '慰霊碑', '歌碑', '石碑', '碑')
STRONG_ENDS = ('大学', '神社', '寺院', '公園', 'モール', 'プラザ', 'ヒルズ', '百貨店',
               '美術館', '博物館', '通り', '城', '山', 'タワー', 'ホール', 'スタジアム')


def filter_scenes(names):
    """Drop office rooms, monuments, and sub-places of an earlier shorter name."""
    kept = []
    for name in names or []:
        name = (name or '').strip()
        if not name or any(mark in name for mark in OFFICE_MARKS):
            continue
        if any(name.endswith(end) for end in MONUMENT_ENDS):
            continue
        if any(len(previous) >= 6 and name.startswith(previous) for previous in kept):
            continue
        kept.append(name)
    return kept


def municipality_of(label):
    """東京都の市または区。地区方位名（東京都23区 / 東京都26市）は市区ではない。"""
    if not label or label.startswith(('東京都26市', '東京都23区', '东京23区', '东京26市')):
        return ''
    match = re.match(r'(東京都.+?[区市])', label)
    return match.group(1) if match else ''


def _with_municipality(municipality, place):
    place = place.strip()
    if municipality and not place.startswith(municipality):
        return municipality + place
    return place


def strong_scene(scenes):
    strong = [name for name in scenes or [] if any(name.endswith(mark) for mark in STRONG_ENDS)]
    short = [name for name in strong if len(name) <= 18]
    if short:
        return short[0]
    return min(strong, key=len) if strong else None


def grid_label(current_name, stations, scenes):
    """Prefer the nearest in-grid station. A town name stays unless the grid had no town."""
    base = re.sub(r'・G\d+$', '', current_name or '').strip()
    municipality = municipality_of(base)
    if stations:
        station = stations[0].strip()
        if not station.endswith('駅'):
            station += '駅'
        return _with_municipality(municipality, station)
    fallback = (not base) or base.startswith(('東京都26市', '東京都23区', '东京23区', '东京26市')) \
        or bool(re.search(r'[東西南北]部$', base))
    if fallback:
        scene = strong_scene(scenes)
        if scene:
            return _with_municipality(municipality, scene)
    return base


def region_label(members, current_name):
    """Rename a region to a station in its own city when that station carries the stores."""
    if not members:
        return None
    municipality = municipality_of(re.sub(r'・R\d+$', '', current_name or ''))
    if not municipality:
        # Placeholder names such as 片区001 carry no ward, so use a strict majority of members.
        found = [name for name in (municipality_of(member['label']) for member in members) if name]
        if not found:
            return None
        municipality, count = sorted(Counter(found).items(), key=lambda item: (-item[1], item[0]))[0]
        if count * 2 <= len(members):
            return None
    weights, counts = Counter(), Counter()
    for member in members:
        label = member['label']
        if label.startswith(municipality) and label.endswith('駅'):
            weights[label] += member['stores']
            counts[label] += 1
    if not weights:
        return None
    label = sorted(weights, key=lambda name: (-weights[name], -counts[name], name))[0]
    total = sum(member['stores'] for member in members) or 1
    if counts[label] >= 2 or weights[label] / total >= 0.35:
        return label
    return None


def keep_pins(poly_m, places, *, buffer_m=BOUNDARY_M):
    """Return places whose coordinates fall inside the grid, in input order."""
    gate = poly_m.buffer(buffer_m)
    kept = []
    outside = 0
    for place in places:
        name = japanese_name(place.get('name'), place.get('language'))
        lat, lon = place.get('lat'), place.get('lon')
        if not name or lat is None or lon is None:
            continue
        pin = to_metric(Point(float(lon), float(lat)))
        if gate.covers(pin):
            kept.append({**place, 'name': name})
        else:
            outside += 1
    return kept, outside
