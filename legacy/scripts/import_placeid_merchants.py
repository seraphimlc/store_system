#!/usr/bin/env python3
"""Upsert Overture/OSM merchant records from the placeid-all source extracts.

Google Places IDs in these files are deliberately ignored. Dry-run by default.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import text

ROOT = Path.home() / "Downloads/placeid-all"
TABLE = "public.kanto_merchants_v20261029"
FILES = ("place_id_tokyo23_wards.jsonl", "place_id_tokyo26_cities.jsonl",
         "place_id_yamanote_full.jsonl", "place_id_chiba.jsonl")
UUID_RE = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$", re.I)


def records(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path.name}:{line_no}") from exc


def identity(source_id: str):
    if UUID_RE.fullmatch(source_id):
        return source_id, source_id, "", "overture"
    match = re.fullmatch(r"osm:(node|way|relation|n|w|r)/(\d+)", source_id)
    if match:
        kind = {"n": "node", "w": "way", "r": "relation"}.get(match.group(1), match.group(1))
        osm_id = f"{kind}/{match.group(2)}"
        return f"osm:{osm_id}", "", osm_id, "osm"
    raise ValueError(f"unsupported source identity: {source_id}")


def load_source():
    grouped = {}
    for filename in FILES:
        for row in records(ROOT / filename):
            src = str(row.get("src_place_id") or "").strip()
            if not src:
                raise ValueError(f"missing src_place_id in {filename}")
            place_id, overture_id, osm_id, source = identity(src)
            name, lat, lon = str(row.get("name") or "").strip(), row.get("lat"), row.get("lon")
            if not name or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                raise ValueError(f"missing name/coordinates for {src}")
            if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError(f"invalid coordinates for {src}")
            item = {"source_id": src, "place_id": place_id, "overture_id": overture_id, "osm_id": osm_id,
                    "source": source, "name": name, "lat": lat, "lon": lon,
                    "address": str(row.get("address") or "").strip(),
                    "municipality": str(row.get("ward") or row.get("city") or row.get("locality") or "").strip(),
                    "region": "chiba" if filename == "place_id_chiba.jsonl" else "tokyo-49",
                    "prefecture": "千葉県" if filename == "place_id_chiba.jsonl" else "東京都",
                    "priority": 0 if source == "overture" else 1}
            old = grouped.get(place_id)
            if old and (old["name"], old["lat"], old["lon"]) != (name, lat, lon):
                # Prefer Overture identity over OSM when both point to one existing entity.
                if item["priority"] < old["priority"]:
                    grouped[place_id] = item
                elif item["priority"] == old["priority"]:
                    raise ValueError(f"conflicting source records for canonical place_id {place_id}")
            elif old is None:
                grouped[place_id] = item
    return grouped


def run(apply: bool):
    from app.db import get_engine

    source = load_source()
    engine = get_engine()
    if engine.dialect.name != "postgresql" or engine.url.database != "store_settle":
        raise RuntimeError("refusing: DATABASE_URL must target PostgreSQL database store_settle")

    with engine.begin() as conn:
        existing = conn.execute(text(f"SELECT place_id,overture_id,osm_id FROM {TABLE}")).all()
        index = {col: defaultdict(set) for col in ("place_id", "overture_id", "osm_id")}
        for place_id, overture_id, osm_id in existing:
            for col, value in (("place_id", place_id), ("overture_id", overture_id), ("osm_id", osm_id)):
                if value:
                    index[col][value].add(place_id)

        targets = defaultdict(list)
        inserts = []
        for place_id, row in source.items():
            match_ids = set(index["place_id"].get(place_id, ()))
            match_ids.update(index["place_id"].get(row["source_id"], ()))
            if row["overture_id"]:
                match_ids.update(index["overture_id"].get(row["overture_id"], ()))
            if row["osm_id"]:
                match_ids.update(index["osm_id"].get(row["osm_id"], ()))
            if len(match_ids) > 1:
                raise ValueError(f"source identity maps to multiple merchants: {row['source_id']}")
            if match_ids:
                targets[next(iter(match_ids))].append(row)
            else:
                inserts.append({**row, "target_en": "", "target_zh": "", "phone": "", "website": "",
                                "confidence": None, "location_accuracy": "pending"})

        updates = []
        for target_id, candidates in targets.items():
            winner = min(candidates, key=lambda row: row["priority"])
            updates.append({**winner, "place_id": target_id})

        report = {"unique_source_place_ids": len(source), "existing_merchants": len(existing),
                  "updated": len(updates), "inserted": len(inserts),
                  "source_collisions_merged_by_priority": sum(len(v) > 1 for v in targets.values()),
                  "mode": "apply" if apply else "dry-run"}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not apply:
            return report

        conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS location_accuracy text NOT NULL DEFAULT 'pending'"))
        conn.execute(text(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS google_place_candidates"))
        conn.execute(text(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS google_place_status"))
        conn.execute(text(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS google_place_id"))
        if updates:
            result = conn.execute(text(f"""UPDATE {TABLE} SET name=:name,lat=:lat,lon=:lon,address=:address,
                municipality=:municipality,region=:region,prefecture=:prefecture,
                location_accuracy=COALESCE(NULLIF(location_accuracy,''),'pending') WHERE place_id=:place_id"""), updates)
            if result.rowcount != len(updates):
                raise RuntimeError(f"expected {len(updates)} updates, got {result.rowcount}")
        if inserts:
            conn.execute(text(f"""INSERT INTO {TABLE}
                (place_id,name,lat,lon,target_en,target_zh,sources,overture_id,osm_id,phone,website,address,
                 confidence,region,prefecture,municipality,location_accuracy)
                VALUES (:place_id,:name,:lat,:lon,:target_en,:target_zh,:source,:overture_id,:osm_id,:phone,:website,
                 :address,:confidence,:region,:prefecture,:municipality,:location_accuracy)"""), inserts)
        total = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar_one()
        unique_ids = conn.execute(text(f"SELECT COUNT(DISTINCT place_id) FROM {TABLE}")).scalar_one()
        if total != len(existing) + len(inserts) or unique_ids != total:
            raise RuntimeError(f"post-import verification failed: rows={total}, unique={unique_ids}")
        statuses = dict(conn.execute(text(
            f"SELECT location_accuracy,COUNT(*) FROM {TABLE} GROUP BY location_accuracy")).all())
        report.update(total_merchants=total, location_accuracy_statuses=statuses)
        print(json.dumps({"verification": report}, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    run(parser.parse_args().apply)
