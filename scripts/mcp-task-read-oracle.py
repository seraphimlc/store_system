"""Old task-query oracle; only schema-only synthetic fixtures, always PostgreSQL read-only."""
import json
import os
from pathlib import Path
import re
import sys
from datetime import date
from urllib.parse import urlparse

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

url = os.environ.get("NEXT_TEST_DATABASE_URL", "")
parsed = urlparse(url)
if parsed.hostname not in ("localhost", "127.0.0.1", "::1") or not re.fullmatch(
        r"/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1", parsed.path):
    raise RuntimeError("Task oracle requires the disposable isolation database")
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "legacy"))
from mcp_service import task_ops  # noqa: E402
from app.services import bd_tasks, bd_teams, date_plan, daily_report  # noqa: E402

payload = json.load(sys.stdin)
today = date.fromisoformat(payload["today"])
for module, name in [(task_ops, "_today"), (bd_tasks, "_today"),
                     (bd_teams, "_today"), (date_plan, "jst_today"), (daily_report, "jst_today")]:
    setattr(module, name, lambda: today)
engine = create_engine(url, connect_args={"options": "-c default_transaction_read_only=on"})
try:
    with Session(engine) as db:
        out = []
        for case in payload["cases"]:
            tool, args = case["tool"], case.get("args", {})
            if tool == "visit_day_report":
                value = task_ops.day_report(db, None, person=case["personCode"], on_date=args.get("date"), action="view")
            elif tool == "visit_my_tasks":
                value = task_ops.my_tasks(db, case["personCode"])
            elif tool == "visit_team_tasks":
                value = task_ops.team_tasks(db, case["teamIds"], **args)
            elif tool == "visit_task_board":
                value = task_ops.board(db, **args)
            else:
                raise ValueError("Unsupported oracle tool")
            out.append(value)
    print(json.dumps(out, ensure_ascii=False))
finally:
    engine.dispose()
