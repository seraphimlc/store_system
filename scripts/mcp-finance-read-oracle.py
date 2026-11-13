"""Legacy payroll/reconciliation read oracle for disposable synthetic schemas only."""
import ast
from datetime import date, datetime
import json
import os
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import create_engine, func, select, case
from sqlalchemy.orm import Session

url = os.environ.get("NEXT_TEST_DATABASE_URL", "")
parsed = urlparse(url)
if parsed.hostname not in ("localhost", "127.0.0.1", "::1") or not re.fullmatch(
        r"/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1", parsed.path):
    raise RuntimeError("Finance oracle requires the schema-only disposable database")
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "legacy"))
import mcp_service as package  # noqa: E402

common = {"Any": Any, "date": date, "datetime": datetime, "func": func, "select": select,
          "case": case, "re": re, "MONTH_PATTERN": r"^[0-9]{4}-(0[1-9]|1[0-2])$"}

def load(name, filename, extra=None):
    source = root / "legacy/mcp_service" / filename
    tree = ast.parse(source.read_text())
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            nodes.append(node)
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__" and not (node.module or "").startswith(("mcp.", "mcp_service")):
            nodes.append(node)
        elif isinstance(node, ast.Import) and not any(alias.name.startswith(("mcp.", "mcp_service")) for alias in node.names):
            nodes.append(node)
        elif isinstance(node, ast.Assign):
            try:
                ast.literal_eval(node.value)
                nodes.append(node)
            except (ValueError, TypeError):
                pass
    module = ModuleType("mcp_service." + name)
    module.__dict__.update({**common, **(extra or {})})
    body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)] + nodes
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(source), "exec"), module.__dict__)
    sys.modules[module.__name__] = module
    setattr(package, name, module)
    return module

capability = load("capability", "capability.py")
reads = load("read_ops", "read_ops.py", {"BadMonth": capability.BadMonth})
recon = load("recon_ops", "recon_ops.py")
source = root / "legacy/mcp_service/scenario_ops.py"
nodes = [node for node in ast.parse(source.read_text()).body if isinstance(node, ast.FunctionDef)
         and node.name in ("_iso", "_recon_interpret_read")]
namespace = dict(common)
body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)] + nodes
exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(source), "exec"), namespace)
engine = create_engine(url, connect_args={"options": "-c default_transaction_read_only=on"})
try:
    results = []
    with Session(engine) as db:
        for item in json.load(sys.stdin):
            args = item.get("args", {})
            month, view = args.get("month"), args.get("view")
            if item["tool"] == "visit_payroll":
                if view in (None, "rows"):
                    value = reads.payroll_rows(db, month)
                elif view == "adjusts":
                    value = recon._recon_adjust_state(db, month)
                    value["settlement"] = recon._recon_settlement(db, month)
                else:
                    value = recon._settlement_trace(db, month=month)
            elif item["tool"] == "visit_recon":
                if view in (None, "status"):
                    value = recon._recon_status(db, month)
                elif view == "diff":
                    value = recon._recon_diff(db, task_id=args.get("task_id"), month=month)
                else:
                    task_id = args.get("task_id")
                    if task_id is None:
                        task_id = recon._current_task_for_month(db, month).id
                    value = namespace["_recon_interpret_read"](db, task_id)
            else:
                raise ValueError("Unsupported oracle tool")
            results.append(value)
    print(json.dumps(results, ensure_ascii=False))
finally:
    engine.dispose()
