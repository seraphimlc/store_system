"""Searchable geographic metadata for operational grids."""
from __future__ import annotations

import unicodedata


def clean_terms(values):
    """Keep an ordered, normalized, unique array of nonempty geographic names."""
    if not isinstance(values, list):
        raise ValueError('车站和关键字必须为数组')
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError('车站和关键字必须为文字数组')
        value = value.strip()
        key = unicodedata.normalize('NFKC', value).casefold()
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def normalized(value):
    return unicodedata.normalize('NFKC', str(value)).casefold()


def matches(keyword, *values):
    needle = normalized(keyword.strip())
    return any(needle in normalized(value) for value in values)


def search_text(values):
    return ' '.join(str(v) for v in [values.get('name') or '', values.get('source_id') or '',
                                    *(values.get('stations') or []), *(values.get('keywords') or [])])


def literal_match(column, keyword):
    value = keyword.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return column.ilike('%' + value + '%', escape='\\')


def grid_match(keyword):
    """JSON is stored as Unicode on PG and escaped Unicode on SQLite; match both."""
    import json
    from sqlalchemy import String, cast, or_
    from app.models import BdWorkGrid
    encoded = json.dumps(keyword, ensure_ascii=True)[1:-1]
    terms = [literal_match(BdWorkGrid.name, keyword), literal_match(BdWorkGrid.source_id, keyword),
             literal_match(cast(BdWorkGrid.id, String), keyword)]
    for column in (BdWorkGrid.stations, BdWorkGrid.keywords):
        terms.append(literal_match(cast(column, String), keyword))
        if encoded != keyword:
            terms.append(literal_match(cast(column, String), encoded))
    return or_(*terms)


def region_search_texts(db, region_ids):
    from app.models import BdRegion, BdWorkGrid
    result = {r.id: r.name for r in db.query(BdRegion.id, BdRegion.name).filter(BdRegion.id.in_(region_ids))}
    for row in db.query(BdWorkGrid.id, BdWorkGrid.zone_id, BdWorkGrid.name, BdWorkGrid.source_id,
                        BdWorkGrid.stations, BdWorkGrid.keywords).filter(
                            BdWorkGrid.zone_id.in_(region_ids), BdWorkGrid.state != 'disabled'):
        result[row.zone_id] += ' ' + str(row.id) + ' ' + search_text(row._asdict())
    return result


def rename_region(db, user, identity, name, revision):
    from datetime import datetime
    from app.models import BdRegion
    from app.services import bd_zone_operations as ops
    ops.require_admin(user)
    name = name.strip()
    if not name or len(name) > 128:
        raise ops.OperationError('片区名称须为1～128个字符')
    row = ops.locked(db, BdRegion, identity)
    ops.revision_ok(row, revision)
    if row.state == 'archived':
        raise ops.OperationError('片区已归档')
    if row.name != name:
        before = row.name
        row.name = name
        row.revision += 1
        row.updated_at = datetime.utcnow()
        ops.audit_log(db, user, 'zone', 'rename', row, {'name': before}, {'name': name})
    return row


def event_match(db, keyword):
    """Match geographic context of an event without broadening its permission scope."""
    from sqlalchemy import and_, or_
    from app.models import BdWorkGrid, BdRegion, BdGridTask, BdRegionTask, BdTaskEvent
    grid_ids=db.query(BdWorkGrid.id).filter(grid_match(keyword))
    task_grids=db.query(BdGridTask.id).filter(BdGridTask.grid_id.in_(grid_ids))
    task_regions=db.query(BdGridTask.region_task_id).filter(BdGridTask.grid_id.in_(grid_ids))
    named_regions=db.query(BdRegionTask.id).join(BdRegion,BdRegion.id==BdRegionTask.region_id).filter(
        literal_match(BdRegion.name,keyword))
    matching_regions=db.query(BdRegionTask.id).filter(or_(BdRegionTask.id.in_(task_regions),
                                                        BdRegionTask.id.in_(named_regions)))
    matching_mains=db.query(BdRegionTask.main_task_id).filter(BdRegionTask.id.in_(matching_regions))
    return or_(BdTaskEvent.grid_task_id.in_(task_grids),BdTaskEvent.region_task_id.in_(named_regions),
        and_(BdTaskEvent.grid_task_id.is_(None),BdTaskEvent.region_task_id.in_(matching_regions)),
        and_(BdTaskEvent.region_task_id.is_(None),BdTaskEvent.grid_task_id.is_(None),
             BdTaskEvent.main_task_id.in_(matching_mains)))
