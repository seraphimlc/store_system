"""Legacy task-plan preview oracle on a disposable schema and synthetic workbook."""
import ast
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlparse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
url = os.environ.get("NEXT_TEST_DATABASE_URL", "")
parsed = urlparse(url)
if parsed.hostname not in ("localhost", "127.0.0.1", "::1") or not re.fullmatch(r"/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1", parsed.path):
    raise RuntimeError("Task-plan oracle requires disposable isolation database")
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "legacy"))
from app.services import bd_task_import
source = root / "legacy/mcp_service/scenario_ops.py"
function = next(node for node in ast.parse(source.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == "_task_import_rows")
namespace = {"Any": Any}
exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
engine = create_engine(url, connect_args={"options": "-c default_transaction_read_only=on"})
try:
    results = []
    with Session(engine) as db:
        for case in json.load(sys.stdin):
            plan = bd_task_import.build_plan(db, case["path"], case.get("layout"))
            results.append({"layout": plan["layout"], "summary": plan["summary"], "teams_in_file": plan["team_names_found"], "rows": namespace["_task_import_rows"](plan)})
    print(json.dumps(results, ensure_ascii=False))
finally:
    engine.dispose()
