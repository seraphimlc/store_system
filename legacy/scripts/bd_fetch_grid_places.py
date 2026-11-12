#!/usr/bin/env python3
"""Fetch station and scene names for one region's grids from Google Places.

Writes a preview JSON only. It does not update bd_work_grid.

Example:
  python scripts/bd_fetch_grid_places.py --region tokyo26
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
import sys

import httpx
from dotenv import load_dotenv
from shapely.geometry import shape
from shapely.validation import make_valid
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv(Path(__file__).resolve().parents[1] / '.env')

from app.services import bd_grid_metadata as metadata
from app.services import bd_grid_places as places

URL = 'https://places.googleapis.com/v1/places:searchNearby'
FIELD_MASK = 'places.id,places.displayName,places.location,places.primaryType'
KINDS = {
    'stations': (places.STATION_TYPES, places.STATION_RADIUS_M, 'DISTANCE'),
    'keywords': (places.LANDMARK_TYPES, places.LANDMARK_RADIUS_M, 'POPULARITY'),
}


def region_sql(region):
    # Older Tokyo 23 releases have no region_code. Later regions always set one.
    if region == 'tokyo23':
        return "(r.source->>'region_code' IS NULL OR r.source->>'region_code' = 'tokyo23')"
    return "r.source->>'region_code' = :region"


def load_grids(region):
    engine = create_engine(os.environ['DATABASE_URL'])
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT g.id, g.name, g.area_km2, g.store_count, g.stations, g.keywords, g.geometry
            FROM bd_work_grid g
            JOIN bd_zone_release r ON r.id = g.release_id
            WHERE {region_sql(region)} AND g.state != 'disabled'
            ORDER BY g.id
        """), {'region': region}).fetchall()
    grids = []
    for row in rows:
        item = dict(row._mapping)
        for column in ('stations', 'keywords', 'geometry'):
            if isinstance(item[column], str):
                item[column] = json.loads(item[column])
        item['stations'] = item['stations'] or []
        item['keywords'] = item['keywords'] or []
        grids.append(item)
    return grids


def proxy_url():
    return (os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')
            or os.environ.get('http_proxy') or os.environ.get('HTTP_PROXY') or None)


def nearby(client, lat, lon, radius, types, rank):
    body = {
        'includedPrimaryTypes': list(types),
        'maxResultCount': 20,
        'languageCode': 'ja',
        'regionCode': 'JP',
        'rankPreference': rank,
        'locationRestriction': {'circle': {
            'center': {'latitude': lat, 'longitude': lon},
            'radius': float(radius),
        }},
    }
    delay = 1.0
    last = None
    for _attempt in range(5):
        try:
            response = client.post(URL, headers={
                'X-Goog-Api-Key': os.environ['GOOGLE_PLACES_API_KEY'],
                'X-Goog-FieldMask': FIELD_MASK,
            }, json=body)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = str(exc)
            time.sleep(delay)
            delay = min(delay * 2, 20)
            continue
        if response.status_code == 200:
            return response.json().get('places') or [], None
        last = response.text[:300]
        if response.status_code not in (429, 500, 502, 503, 504):
            return [], last
        time.sleep(delay)
        delay = min(delay * 2, 20)
    return [], last or 'retry exhausted'


def parse_places(payload):
    parsed = []
    for place in payload:
        loc = place.get('location') or {}
        display = place.get('displayName') or {}
        if loc.get('latitude') is None or loc.get('longitude') is None:
            continue
        parsed.append({
            'id': place.get('id') or '',
            'name': display.get('text') or '',
            'language': display.get('languageCode') or '',
            'lat': loc['latitude'],
            'lon': loc['longitude'],
            'primary_type': place.get('primaryType') or '',
        })
    return parsed


def polygon_of(grid):
    geom = shape(grid['geometry'])
    if not geom.is_valid:
        geom = make_valid(geom)
    return geom, places.to_metric(geom)


def collect_kind(client, poly_m, kind):
    types, radius, rank = KINDS[kind]
    samples = places.sample_points(poly_m, radius)
    used_radius = places.circle_radius(poly_m, radius)
    found = []
    seen = set()
    outside = 0
    calls = 0
    errors = []
    queried = set()
    queue = [(sample, used_radius if len(samples) == 1 else radius) for sample in samples]
    while queue and calls < 24:
        point, rad = queue.pop(0)
        key = (round(point.x, 0), round(point.y, 0), round(rad))
        if key in queried:
            continue
        queried.add(key)
        lonlat = places.to_lonlat(point)
        payload, error = nearby(client, lonlat.y, lonlat.x, rad, types, rank)
        calls += 1
        if error:
            errors.append(error)
            continue
        if kind == 'stations' and len(payload) >= 20 and rad > 700 and calls + 4 <= 24:
            for dx, dy in ((rad / 2, rad / 2), (rad / 2, -rad / 2), (-rad / 2, rad / 2), (-rad / 2, -rad / 2)):
                child = places._pull_inside(poly_m, point.x + dx, point.y + dy, rad)
                if child is not None:
                    queue.append((child, rad / 2))
        kept, missed = places.keep_pins(poly_m, parse_places(payload))
        outside += missed
        for place in kept:
            identity = place['id'] or place['name']
            if identity in seen:
                continue
            seen.add(identity)
            found.append(place)
    return found, calls, outside, errors


def collect_grid(client, grid):
    _geom, poly_m = polygon_of(grid)
    stations, station_calls, station_out, station_errors = collect_kind(client, poly_m, 'stations')
    landmarks, landmark_calls, landmark_out, landmark_errors = collect_kind(client, poly_m, 'keywords')
    station_names = metadata.clean_terms([place['name'] for place in stations])
    taken = {metadata.normalized(name) for name in station_names}
    keywords = metadata.clean_terms([
        place['name'] for place in landmarks if metadata.normalized(place['name']) not in taken])
    return {
        'id': grid['id'],
        'name': grid['name'],
        'area_km2': round(grid['area_km2'] or 0, 3),
        'store_count': grid['store_count'],
        'stations': station_names,
        'keywords': keywords,
        'calls': station_calls + landmark_calls,
        'outside_pins': station_out + landmark_out,
        'errors': station_errors + landmark_errors,
    }


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', default='tokyo26')
    parser.add_argument('--out', type=Path, default=Path('data/bd_zones/tokyo26-places-preview.json'))
    parser.add_argument('--limit', type=int, default=0, help='只跑前 N 个网格，0 表示全部')
    args = parser.parse_args()
    if not os.environ.get('GOOGLE_PLACES_API_KEY'):
        parser.error('GOOGLE_PLACES_API_KEY 未配置')
    if not os.environ.get('DATABASE_URL'):
        parser.error('DATABASE_URL 未配置')
    grids = load_grids(args.region)
    if not grids:
        parser.error(f'{args.region} 没有网格')
    if args.limit:
        grids = grids[:args.limit]
    checkpoint_path = args.out.with_suffix('.checkpoint.json')
    done = {}
    if checkpoint_path.exists():
        previous = json.loads(checkpoint_path.read_text())
        if previous.get('region') == args.region:
            done = {row['id']: row for row in previous.get('grids', [])}
    print(f'{args.region}: {len(grids)} grids, {len(done)} already fetched', flush=True)
    rows = []
    with httpx.Client(timeout=30, proxy=proxy_url()) as client:
        for index, grid in enumerate(grids, start=1):
            if grid['id'] in done and not done[grid['id']].get('errors'):
                rows.append(done[grid['id']])
                continue
            started = time.time()
            row = collect_grid(client, grid)
            rows.append(row)
            done[row['id']] = row
            write_json(checkpoint_path, {'region': args.region, 'grids': rows})
            print(f"{index}/{len(grids)} id={row['id']} area={row['area_km2']} "
                  f"stations={len(row['stations'])} keywords={len(row['keywords'])} "
                  f"calls={row['calls']} outside={row['outside_pins']} "
                  f"errors={len(row['errors'])} {time.time() - started:.1f}s", flush=True)
    preview = {
        'region': args.region,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source': 'Google Places API Nearby Search (New)',
        'grids': rows,
        'summary': {
            'grids': len(rows),
            'calls': sum(row['calls'] for row in rows),
            'with_stations': sum(bool(row['stations']) for row in rows),
            'with_keywords': sum(bool(row['keywords']) for row in rows),
            'station_terms': sum(len(row['stations']) for row in rows),
            'keyword_terms': sum(len(row['keywords']) for row in rows),
            'errors': sum(len(row['errors']) for row in rows),
        },
    }
    write_json(args.out, preview)
    print(json.dumps(preview['summary'], ensure_ascii=False), flush=True)
    print(args.out, flush=True)


if __name__ == '__main__':
    main()
