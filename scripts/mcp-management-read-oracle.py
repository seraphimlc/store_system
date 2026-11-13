"""Read-only legacy file/account/config/store oracle on synthetic disposable schemas."""
import ast
from datetime import date, datetime
import json
import os
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
from typing import Any
from urllib.parse import urlparse
from sqlalchemy import create_engine, func, select, case
from sqlalchemy.orm import Session

url = os.environ.get("NEXT_TEST_DATABASE_URL", "")
parsed = urlparse(url)
if parsed.hostname not in ("localhost", "127.0.0.1", "::1") or not re.fullmatch(
        r"/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1", parsed.path):
    raise RuntimeError("Management oracle requires a schema-only disposable database")
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "legacy"))
import mcp_service as package
common = {"Any": Any, "date": date, "datetime": datetime, "func": func, "select": select,
          "case": case, "re": re, "MONTH_PATTERN": r"^[0-9]{4}-(0[1-9]|1[0-2])$"}

def load(name, extra=None):
    source = root / "legacy/mcp_service" / (name + ".py")
    nodes = []
    for node in ast.parse(source.read_text()).body:
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

capability = load("capability")
reads = load("read_ops", {"BadMonth": capability.BadMonth})
misc = load("misc_ops")
source = root / "legacy/mcp_service/scenario_ops.py"
nodes = [node for node in ast.parse(source.read_text()).body if isinstance(node, ast.FunctionDef)
         and node.name in ("_entity_summary", "_store_pairs")]
namespace = {**common, "guards": SimpleNamespace(GuardError=reads.BadParam)}
body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)] + nodes
exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(source), "exec"), namespace)
engine = create_engine(url, connect_args={"options": "-c default_transaction_read_only=on"})
try:
    out = []
    with Session(engine) as db:
        for item in json.load(sys.stdin):
            tool, args = item["tool"], item.get("args", {})
            if tool == "visit_files":
                view = args.get("view", "list")
                if view == "layout":
                    result = misc.file_layout(db, args["import_id"])
                elif view == "report":
                    result = reads.file_report(db, args["import_id"], bucket=None)
                elif view == "tasks":
                    result = reads.list_tasks(db, month=args.get("month"), kind=args.get("kind"), status=args.get("status"))
                else:
                    result = reads.file_list(db, month=args.get("month"))
            elif tool == "visit_verify":
                result = reads.verify_integrity(db)
            elif tool == "visit_staff":
                result = reads.staff_list(db, status=args.get("status"))
            elif tool == "visit_config":
                result = reads.config_get(db)
            elif tool == "visit_store":
                result = namespace["_store_pairs"](db, args.get("kind")) if args.get("view") == "pairs" else reads.store_search(db, args.get("q", ""), limit=args.get("limit", 20))
            else:
                raise ValueError("Unsupported oracle tool")
            out.append(result)
    print(json.dumps(out, ensure_ascii=False))
finally:
    engine.dispose()
