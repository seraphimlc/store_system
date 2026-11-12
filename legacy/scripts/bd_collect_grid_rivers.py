#!/usr/bin/env python3
"""为现有网格补采公开OSM河道面；只写地理快照，不修改业务数据。"""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def collect(db, output):
    from shapely.geometry import shape
    from app.models import BdWorkGrid
    from scripts.bd_collect_zones import fetch
    bounds = [shape(geometry).bounds for geometry, in db.query(BdWorkGrid.geometry)]
    if not bounds:
        raise ValueError('没有可用于确定采集范围的网格')
    west, south = min(b[0] for b in bounds), min(b[1] for b in bounds)
    east, north = max(b[2] for b in bounds), max(b[3] for b in bounds)
    bbox = f'{south-.001:.6f},{west-.001:.6f},{north+.001:.6f},{east+.001:.6f}'
    query = ('[out:json][timeout:150];('
             f'nwr["natural"="water"]["water"="river"]({bbox});'
             f'nwr["waterway"="riverbank"]({bbox}););out geom;')
    output.parent.mkdir(parents=True, exist_ok=True)
    document = fetch(query, output)
    return {'snapshot': str(output), 'elements': len(document['elements']), 'bbox': bbox}


if __name__ == '__main__':
    from app.db import SessionLocal
    from app.services import bd_zones
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=bd_zones.data_dir() / 'grid-rivers.roads-v1.source.json')
    args = parser.parse_args()
    with SessionLocal() as db:
        print(json.dumps(collect(db, args.output), ensure_ascii=False), flush=True)
