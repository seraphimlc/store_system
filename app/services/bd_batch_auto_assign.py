"""Preview and apply contiguous, load-balanced grid assignments in a batch region."""
from __future__ import annotations

import hashlib
import hmac
import time
import heapq
import json
import math
from datetime import datetime, timedelta

from sqlalchemy import func
from shapely.geometry import shape

from app.models import (BdGridTask, BdMainTask, BdRegionTask, BdStoreTask,
                        BdTaskEvent, BdWorkGrid, BdTeamMember, User)
from app.services import bd_hierarchical_tasks as tasks
from app.services import bd_zone_operations as ops
from app.services import ai_chat
from app.config import get_settings


def _members(db, rt):
    rows = (db.query(User).join(BdTeamMember, BdTeamMember.person_code == User.person_code)
            .filter(BdTeamMember.team_id == rt.team_id, BdTeamMember.role == 'member',
                    BdTeamMember.end_date.is_(None), User.is_active.is_(True),
                    User.status == 'active', User.role.in_(('staff', 'leader')))
            .order_by(User.id).all())
    if not rows:
        raise ops.OperationError('团队没有在岗队员')
    return rows


def _graph(rows):
    geoms = [shape(row[0].snapshot.get('geometry') or row[1].geometry) for row in rows]
    centers = [geom.representative_point() for geom in geoms]
    graph = [set() for _ in rows]
    for i, geom in enumerate(geoms):
        for j in range(i + 1, len(geoms)):
            if geom.boundary.intersection(geoms[j].boundary).length > 1e-9:
                graph[i].add(j)
                graph[j].add(i)
    component = {0} if rows else set()
    queue = list(component)
    while queue:
        for nxt in graph[queue.pop()] - component:
            component.add(nxt)
            queue.append(nxt)
    if len(component) != len(rows):
        raise ops.OperationError('网格存在不连通范围，暂不能保证队员片区连续')
    return geoms, centers, graph


def _loads(db, rt, main, member_ids, grid_tasks, include_batch, use_efficiency):
    workloads = {identity: 0 for identity in member_ids}
    if include_batch:
        other_ids = (db.query(BdGridTask.id).join(BdRegionTask, BdRegionTask.id == BdGridTask.region_task_id)
                     .filter(BdRegionTask.main_task_id == main.id, BdRegionTask.id != rt.id,
                             BdGridTask.assignee_id.in_(member_ids), BdGridTask.status != 'completed').all())
        ids = [row[0] for row in other_ids]
        if ids:
            for identity, count in (db.query(BdGridTask.assignee_id, BdStoreTask.id)
                    .join(BdStoreTask, BdStoreTask.grid_task_id == BdGridTask.id)
                    .filter(BdGridTask.id.in_(ids), BdStoreTask.status != 'completed')
                    .group_by(BdGridTask.assignee_id, BdStoreTask.id).all()):
                workloads[identity] += 1
    efficiencies = {identity: 1.0 for identity in member_ids}
    if grid_tasks and use_efficiency:
        rows = (db.query(BdTaskEvent.actor_id, func.date(BdTaskEvent.created_at),
                         func.count(BdTaskEvent.id))
                .filter(BdTaskEvent.event_type == 'complete_store_task',
                        BdTaskEvent.actor_id.in_(member_ids), BdTaskEvent.main_task_id != main.id,
                        BdTaskEvent.created_at >= datetime.utcnow() - timedelta(days=60))
                .group_by(BdTaskEvent.actor_id, func.date(BdTaskEvent.created_at)).all())
        counts = {identity: 0 for identity in member_ids}
        active_days = {identity: 0 for identity in member_ids}
        for identity, _day, count in rows:
            counts[identity] += count
            active_days[identity] += 1
        rates = {identity: counts[identity] / active_days[identity]
                 for identity in member_ids if counts[identity] >= 5 and active_days[identity]}
        baseline = sum(rates.values()) / len(rates) if rates else 0
        if baseline:
            for identity, rate in rates.items():
                efficiencies[identity] = min(1.5, max(.67, rate / baseline))
    return workloads, efficiencies


def _validate_decision(decision, member_ids):
    """Fail closed on missing/extra members, non-finite weights and expired previews."""
    if not isinstance(decision, dict) or set(decision) != {'weights', 'reason', 'expires_at'}:
        raise ops.OperationError('AI分配建议格式无效，请重新生成')
    weights = decision['weights']
    if (not isinstance(weights, dict) or set(weights) != {str(i) for i in member_ids}
            or any(type(v) not in (int, float) or not math.isfinite(v) or not .75 <= v <= 1.25
                   for v in weights.values())
            or not isinstance(decision['reason'], str) or not 1 <= len(decision['reason'].strip()) <= 1500
            or type(decision['expires_at']) is not int):
        raise ops.OperationError('AI分配建议格式无效，请重新生成')
    if decision['expires_at'] < time.time():
        raise ops.OperationError('AI分配预览已过期，请重新生成')
    return decision


def _ask_ai(rows, members, workloads, efficiencies, include_batch, use_efficiency):
    if not ai_chat.configured():
        raise ops.OperationError('AI未配置，请管理员配置AI_API_KEY、AI_BASE_URL和AI_MODEL')
    _geoms, centers, graph = _graph(rows)
    # Only pseudonymous IDs and numeric summaries leave the server; no names,
    # merchant details, keys or complete boundary coordinates are sent.
    center = centers[0]
    payload = {
        'include_batch_load': include_batch, 'use_historical_efficiency': use_efficiency,
        'members': [{'member_id': m.id, 'other_pending': workloads[m.id],
                     'historical_efficiency': efficiencies[m.id]} for m in members],
        'grids': [{'id': gt.id, 'pending': weight,
                   'relative_center': [round(centers[i].x-center.x, 5), round(centers[i].y-center.y, 5)],
                   'neighbors': sorted(rows[j][0].id for j in graph[i])}
                  for i, (gt, _grid, weight) in enumerate(rows)]}
    from app.i18n import CURRENT_LANG
    language = '日文' if CURRENT_LANG.get() == 'ja' else '中文'
    prompt = (
        '你是巡店派工决策助手。根据以下数值摘要，为每个队员提出任务容量修正权重。'
        '这些权重会实际影响共边连续扩张算法，不是仅供解释。'
        '目标：避免零散或过长作业范围，并平衡(其他片区待办+本次待办)/历史效率。'
        '队员历史效率是有完成记录日的日均产量代理，并非工时；不得臆测个人能力。'
        '其他片区负载与历史效率由程序另行计入，勿重复补偿。'
        '未启用历史效率时不得按个人能力倾斜；仅在空间约束与已有负载需要时小幅修正。'
        '每名队员必须恰好出现一次，权重必须在0.75到1.25之间，标准值1。'
        '程序将验证合法性并生成具体网格归属，不能要求绕过约束。'
        f'严格只返回JSON：{{"weights":{{"队员ID":1.0}},"reason":"用{language}说明建议、取舍和数据局限"}}。'
        '不要输出网格分配，不要markdown。\n' + json.dumps(payload, ensure_ascii=False))
    try:
        raw = ai_chat.chat(prompt, timeout=60, retries=0, max_tokens=4000)
        decision = json.loads(raw)
        if not isinstance(decision, dict) or set(decision) != {'weights', 'reason'}:
            raise ValueError('invalid response')
        decision['expires_at'] = int(time.time()) + 900
        return _validate_decision(decision, [m.id for m in members])
    except ops.OperationError:
        raise
    except Exception:
        # Do not expose provider errors that could contain headers/URLs/secrets.
        raise ops.OperationError('AI生成分配建议失败，请重试；未自动回退到算法方案') from None


def _solve(rows, members, workloads, efficiencies, use_efficiency, ai_weights=None):
    count = len(rows)
    if not count:
        return {}
    geoms, centers, graph = _graph(rows)
    capacity = {m.id: (efficiencies[m.id] if use_efficiency else 1.0) *
                ((ai_weights or {}).get(str(m.id), 1.0)) for m in members}
    total_capacity = sum(capacity.values())
    total_load = sum(max(1, row[2]) for row in rows)
    all_load = total_load + sum(workloads.values())
    targets = {m.id: all_load * capacity[m.id] / total_capacity for m in members}
    active_members = [m for m in members if count >= len(members)]
    if not active_members:
        active_members = sorted(members, key=lambda m: (workloads[m.id], m.id))[:count]
        active_load = total_load + sum(workloads[m.id] for m in active_members)
        targets = {m.id: active_load * capacity[m.id] / sum(capacity[x.id] for x in active_members)
                   for m in active_members}

    # Spread initial seeds spatially, then grow each territory only from its frontier.
    first = min(range(count), key=lambda i: (centers[i].x, centers[i].y, rows[i][0].id))
    seeds = [first]
    while len(seeds) < len(active_members):
        seeds.append(max((i for i in range(count) if i not in seeds),
                         key=lambda i: (min(centers[i].distance(centers[s]) for s in seeds), -rows[i][0].id)))
    owners = {seed: active_members[index].id for index, seed in enumerate(seeds)}
    assigned = {member.id: max(1, rows[seed][2]) for member, seed in zip(active_members, seeds)}
    queue = []

    def score(owner, distance):
        load_penalty = (workloads[owner] + assigned[owner]) / max(targets[owner], 1)
        return distance * (1 + load_penalty) / capacity[owner]

    def offer(owner, source, node, distance):
        heapq.heappush(queue, (score(owner, distance), rows[node][0].id,
                               owner, source, node, distance))

    for node, owner in owners.items():
        for nxt in graph[node]:
            if nxt not in owners:
                offer(owner, node, nxt, centers[node].distance(centers[nxt]))
    while len(owners) < count:
        while queue:
            priority, _, owner, source, node, distance = heapq.heappop(queue)
            if node not in owners:
                refreshed = score(owner, distance)
                if not math.isclose(priority, refreshed, rel_tol=1e-9, abs_tol=1e-12):
                    heapq.heappush(queue, (refreshed, rows[node][0].id, owner, source, node, distance))
                    continue
                break
        else:
            raise ops.OperationError('网格连通关系异常，无法生成完整分配')
        owners[node] = owner
        assigned[owner] += max(1, rows[node][2])
        for nxt in graph[node]:
            if nxt not in owners:
                offer(owner, node, nxt, distance + centers[node].distance(centers[nxt]))
    return {rows[index][0].id: owner for index, owner in owners.items()}


def plan(db, user, region_task_id, *, include_batch=True, use_efficiency=False, member_ids=None,
         _decision=None):
    if type(include_batch) is not bool or type(use_efficiency) is not bool:
        raise ops.OperationError('分配配置必须为布尔值')
    initial = db.get(BdRegionTask, region_task_id)
    if not initial:
        raise ops.OperationError('片区任务不存在')
    main = db.get(BdMainTask, initial.main_task_id)
    rt = db.query(BdRegionTask).filter_by(id=region_task_id).populate_existing().one()
    tasks.captain(db, user, rt)
    if main:
        tasks.require_enabled(main)
    if not main or main.status == 'completed' or rt.status == 'completed':
        raise ops.OperationError('已完成批次不能重新分配')
    members = _members(db, rt)
    if member_ids is not None:
        if not isinstance(member_ids, list) or any(type(i) is not int for i in member_ids):
            raise ops.OperationError('队员选择无效，请刷新后重试')
        requested = {int(identity) for identity in member_ids}
        if not requested or requested - {member.id for member in members}:
            raise ops.OperationError('队员选择无效，请刷新后重试')
        members = [member for member in members if member.id in requested]
    rows = (db.query(BdGridTask, BdWorkGrid)
            .join(BdWorkGrid, BdWorkGrid.id == BdGridTask.grid_id)
            .filter(BdGridTask.region_task_id == rt.id, BdGridTask.status != 'completed')
            .order_by(BdGridTask.id).all())
    counts = dict(db.query(BdStoreTask.grid_task_id, func.count(BdStoreTask.id))
                  .filter(BdStoreTask.grid_task_id.in_([g.id for g, _ in rows]))
                  .filter(BdStoreTask.status != 'completed').group_by(BdStoreTask.grid_task_id).all()) if rows else {}
    rows = [(gt, grid, counts.get(gt.id, 0)) for gt, grid in rows]
    if not rows:
        raise ops.OperationError('当前片区没有待分配网格')
    member_ids = [m.id for m in members]
    workloads, efficiencies = _loads(db, rt, main, member_ids, rows, include_batch, use_efficiency)
    decision = (_ask_ai(rows, members, workloads, efficiencies, include_batch, use_efficiency)
                if _decision is None else _validate_decision(_decision, member_ids))
    assignment = _solve(rows, members, workloads, efficiencies, use_efficiency, decision['weights'])
    summary = {m.id: {'member_id': m.id, 'name': m.display_name or m.username,
                      'grid_count': 0, 'workload': 0, 'other_batch_workload': workloads[m.id],
                      'ai_weight': decision['weights'][str(m.id)], 'efficiency': round(efficiencies[m.id], 2)} for m in members}
    for gt, _grid, weight in rows:
        item = summary[assignment[gt.id]]
        item['grid_count'] += 1
        item['workload'] += weight
    state = {'algorithm': 'ai-contiguous-v2', 'user_id': user.id, 'region_task_id': rt.id,
             'main_task_id': main.id, 'region_revision': rt.revision, 'main_revision': main.revision,
             'ai_decision': decision,
             'grids': [(gt.id, gt.revision, gt.status, gt.assignee_id, weight,
                        gt.snapshot.get('geometry') or grid.geometry) for gt, grid, weight in rows],
             'members': member_ids, 'include_batch': bool(include_batch),
             'use_efficiency': bool(use_efficiency), 'assignment': sorted(assignment.items()),
             'workloads': workloads, 'efficiencies': efficiencies}
    signature = hmac.new(get_settings().secret_key.encode(),
                         json.dumps(state, sort_keys=True).encode(), hashlib.sha256).hexdigest()
    return {'signature': signature, 'ai_decision': decision, 'ai_reason': decision['reason'],
            'assignments': {str(key): value for key, value in assignment.items()}, 'members': list(summary.values()),
            'grid_count': len(rows), 'workload': sum(row[2] for row in rows),
            'locked_completed': db.query(BdGridTask.id).filter_by(region_task_id=rt.id,
                                                                  status='completed').count(),
            'basis': ['空间连续优先', '按未完成商户任务数平衡'] +
                     (['计入同批次其他片区负载'] if include_batch else []) +
                     (['按历史效率调整目标量'] if use_efficiency else []),
            'efficiency_note': '按最近60天有完成记录工作日的日均完成量估算；样本不足5件时按均衡效率。' if use_efficiency else ''}


def apply(db, user, region_task_id, signature, *, include_batch=True, use_efficiency=False, member_ids=None,
          ai_decision=None):
    initial = db.get(BdRegionTask, region_task_id)
    if not initial:
        raise ops.OperationError('片区任务不存在')
    main = ops.locked(db, BdMainTask, initial.main_task_id)
    rt = ops.locked(db, BdRegionTask, region_task_id)
    tasks.captain(db, user, rt)
    grids = (db.query(BdGridTask).filter_by(region_task_id=rt.id)
             .order_by(BdGridTask.id).with_for_update().all())
    if ai_decision is None:
        raise ops.OperationError('缺少AI分配预览，请重新生成')
    current = plan(db, user, rt.id, include_batch=include_batch, use_efficiency=use_efficiency,
                   member_ids=member_ids, _decision=ai_decision)
    if not isinstance(signature, str) or not hmac.compare_digest(current['signature'], signature):
        raise ops.OperationError('预览数据已变化，请重新生成分配方案')
    for gt in grids:
        target = current['assignments'].get(str(gt.id))
        if target is None or gt.status == 'completed':
            continue
        before = {'assignee_id': gt.assignee_id}
        gt.assignee_id = target
        gt.status = 'in_progress' if gt.status == 'in_progress' else 'assigned'
        gt.revision += 1
        ops.audit_log(db, user, 'task', 'auto_assign_grid', gt, before,
                      {'assignee_id': target, 'algorithm': 'ai-contiguous-v2',
                       'ai_decision': current['ai_decision'],
                       'region_task_id': rt.id, 'main_task_id': main.id})
    return current
