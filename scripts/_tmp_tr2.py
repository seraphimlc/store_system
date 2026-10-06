# -*- coding: utf-8 -*-
"""转队：路由 + 两个界面入口（未分配批量转 / 任务详情页单条转）+ i18n"""


def sub(path, pairs):
    s = open(path, encoding='utf-8').read()
    for a, b in pairs:
        assert a in s, '%s 找不到: %s' % (path, a[:50])
        s = s.replace(a, b, 1)
    open(path, 'w', encoding='utf-8').write(s)
    print('  OK', path)


# ① 路由：/my/tasks/transfer
ROUTE = '''@router.post("/my/tasks/transfer")
def my_tasks_transfer(request: Request, task_id: Optional[List[str]] = Form(None),
                      to_team: str = Form(""), tab: str = Form("unassigned"),
                      back: str = Form(""), csrf_token: str = Form(""),
                      user: Optional[User] = Depends(require_login),
                      db: Session = Depends(get_db)):
    """**队长把本队任务转给别的队**（用户 2026-10-06："队长之间可以私下交换任务"）。

    口径：不用对方确认、直接过去；未分配 + 进行中都能转（已完成不转）；
    转出**移出原担当**但**保留已上报进度**；给对方队长和被移出的担当各发一条消息。
    管理员走已有的 `/tasks/reassign`（同一服务层）。
    """
    g = _staff_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_tasks
    ids = [int(x) for x in (task_id or []) if str(x).strip().isdigit()]
    back = _safe_back(back, "/my/tasks?tab=%s" % (tab or "unassigned"))
    if not ids or not str(to_team).strip().isdigit():
        return RedirectResponse(_with_msg(back, "err", _m("请先勾选任务并选择要转给哪个队")),
                                status_code=303)
    try:
        r = bd_tasks.transfer_team_task(db, user, ids, int(to_team),
                                        by=user.username, actor_user=user)
        db.commit()
    except bd_tasks.TaskError as e:
        db.rollback()
        return RedirectResponse(_with_msg(back, "err", _m(str(e))), status_code=303)
    except Exception as e:                            # noqa: BLE001
        db.rollback()
        return RedirectResponse(_with_msg(back, "err", _m(str(e))), status_code=303)
    txt = _m("已转给 %s：%d 个任务", r["to_team"], r["transferred"])
    if r["cleared"]:
        txt += _m("（移出 %d 个担当，进度保留）", r["cleared"])
    return RedirectResponse(_with_msg(back, "msg", txt), status_code=303)


'''
p = 'app/routers/bd_r.py'
s = open(p, encoding='utf-8').read()
anchor = '@router.post("/my/tasks/progress-bulk")'
assert anchor in s, 'progress-bulk 锚点'
s = s.replace(anchor, ROUTE + anchor, 1)
open(p, 'w', encoding='utf-8').write(s)
import ast
ast.parse(s)
print('  OK 路由 /my/tasks/transfer')

# ② 未分配批量工具条：选队伍 + 转给该队
p2 = 'app/templates/my_tasks.html'
s2 = open(p2, encoding='utf-8').read()
anchor2 = '''      <button class="btn ghost" type="submit" name="pct" value="100"
              formaction="/my/tasks/progress-bulk"
              onclick="return confirm('{{ t('把勾选的任务全部标成已完成？') }}')"
              data-testid="bulk-done">{{ t('批量标记完成') }}</button>'''
assert anchor2 in s2, 'bulk-done 锚点'
NEW2 = anchor2 + '''
      {# 队长之间私下换活：勾几条 → 选队伍 → 转过去（用户 2026-10-06） #}
      {% if transfer_teams %}
      <label class="f" style="gap:.3rem">{{ t('转给') }}
        <select name="to_team" data-testid="to-team" style="max-width:9rem">
          <option value="">{{ t('选队伍') }}</option>
          {% for tm in transfer_teams %}<option value="{{ tm.id }}">{{ tm.name }}</option>{% endfor %}
        </select></label>
      <button class="btn ghost" type="submit" value=""
              formaction="/my/tasks/transfer"
              onclick="return confirm('{{ t('转出后这些任务归对方队；已派的担当会被移出（已上报进度保留）。确定？') }}')"
              data-testid="bulk-transfer">{{ t('转给该队') }}</button>
      {% endif %}'''
s2 = s2.replace(anchor2, NEW2, 1)
open(p2, 'w', encoding='utf-8').write(s2)
print('  OK my_tasks.html 批量转队')

# ③ 任务详情页：管理员改派块扩展到"本队队长也能转"
p3 = 'app/templates/bd_task_detail.html'
s3 = open(p3, encoding='utf-8').read()
old3 = '''{# 管理员：改派队伍 / 退回车站池（2026-10-06 审计：派队原本单向不可逆，选错只能改库） #}
{% if current_user and current_user.role == 'admin' and r and r.task.team_id %}'''
new3 = '''{# 管理员改派 / **本队队长转队**（2026-10-06：队长之间私下换活）+ 退回车站池 #}
{% if current_user and r and r.task.team_id and (current_user.role == 'admin' or can_transfer) %}'''
assert old3 in s3, '详情页 admin 块'
s3 = s3.replace(old3, new3, 1)
old4 = '''  <form method="post" action="/tasks/reassign" style="display:inline-flex;gap:.4rem;align-items:center">'''
new4 = '''  {% set _is_admin = current_user.role == 'admin' %}
  <form method="post" action="{{ '/tasks/reassign' if _is_admin else '/my/tasks/transfer' }}"
        style="display:inline-flex;gap:.4rem;align-items:center">'''
assert old4 in s3, '详情页 reassign form'
s3 = s3.replace(old4, new4, 1)
old5 = '''    <span class="hint">{{ t('改派队伍') }}：</span>
    <select name="team_id" data-testid="reassign-team" style="max-width:13rem">'''
new5 = '''    <span class="hint">{{ t('改派队伍') if _is_admin else t('转给别的队') }}：</span>
    <select name="{{ 'team_id' if _is_admin else 'to_team' }}" data-testid="reassign-team"
            style="max-width:13rem">'''
assert old5 in s3, '详情页 select'
s3 = s3.replace(old5, new5, 1)
old6 = '''    <button class="btn ghost" type="submit" data-testid="reassign-btn"
            onclick="return confirm('改派会清空不属于新队的担当，确定？')">{{ t('改派') }}</button>'''
new6 = '''    <button class="btn ghost" type="submit" data-testid="reassign-btn"
            onclick="return confirm('{{ t('转出后这条任务归对方队；已派的担当会被移出（已上报进度保留）。确定？') }}')">{{ t('改派') if _is_admin else t('转出') }}</button>'''
assert old6 in s3, '详情页按钮'
s3 = s3.replace(old6, new6, 1)
# 退回车站池只给管理员（原样保留）
old7 = '''  <form method="post" action="/tasks/return-pool" style="display:inline-flex;margin-left:1rem">'''
new7 = '''  {% if _is_admin %}
  <form method="post" action="/tasks/return-pool" style="display:inline-flex;margin-left:1rem">'''
assert old7 in s3
s3 = s3.replace(old7, new7, 1)
# 该 form 的收尾加 endif（找到它的 </form> 后的第一处，用唯一的 action 定位后文）
i = s3.index(new7)
j = s3.index('</form>', i) + len('</form>')
s3 = s3[:j] + '\n  {% endif %}' + s3[j:]
open(p3, 'w', encoding='utf-8').write(s3)
print('  OK bd_task_detail.html 支持队长转队')

# ③b 详情页路由：传 can_transfer
sub('app/routers/bd_r.py', [
    ('        "can_adjust": bd_tasks.can_adjust(db, user, t),',
     '''        "can_adjust": bd_tasks.can_adjust(db, user, t),
        # 本队队长可以"转给别的队"（已完成的除外，用户 2026-10-06）
        "can_transfer": bool(
            t.state != bd_tasks.STATE_DONE
            and bd_teams.is_leader_of(db, getattr(user, "person_code", None),
                                      t.team_id)),'''),
])

# ④ 路由上下文：转队下拉（排除自己队）+ import 别名
sub('app/routers/bd_r.py', [
    ('        "is_leader": is_leader, "teams": teams, "rows": rows,',
     '''        "is_leader": is_leader, "teams": teams, "rows": rows,
        # 队长转队的目标队（排除本队，用户 2026-10-06）
        "transfer_teams": ([x for x in teams if x["id"] not in set(team_ids)]
                           if is_leader else []),'''),
])
