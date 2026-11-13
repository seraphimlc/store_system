"""Read legacy MCP projections on the isolation runner's synthetic database only."""
import ast
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from datetime import date
from types import SimpleNamespace, ModuleType
from urllib.parse import urlparse

from sqlalchemy import create_engine, func, select, case
from sqlalchemy.orm import Session

url = os.environ.get("NEXT_TEST_DATABASE_URL", "")
parsed = urlparse(url)
if parsed.hostname not in ("localhost", "127.0.0.1", "::1") or not re.fullmatch(
    r"/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1", parsed.path
):
    raise RuntimeError("Legacy MCP oracle requires the schema-only isolation database")
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "legacy"))
# Execute unchanged legacy financial function bodies without importing its obsolete MCP SDK.
my_source = root / "legacy/mcp_service/my_ops.py"
my_tree = ast.parse(my_source.read_text())
pattern_source = root / "legacy/mcp_service/capability.py"
pattern = next(ast.literal_eval(node.value) for node in ast.parse(pattern_source.read_text()).body
               if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "MONTH_PATTERN" for target in node.targets))
my_namespace = {"Any": Any, "date": date, "func": func, "MONTH_PATTERN": pattern, "BadMonth": ValueError}
my_functions = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
                         + [node for node in my_tree.body if isinstance(node, ast.FunctionDef)], type_ignores=[])
exec(compile(ast.fix_missing_locations(my_functions), str(my_source), "exec"), my_namespace)
my_ops = SimpleNamespace(**my_namespace)

# Load unchanged legacy read functions with their original module names, without MCP transport imports.
import mcp_service as legacy_package

def load_read_module(name, path, namespace):
    module = ModuleType(name)
    module.__dict__.update(namespace)
    nodes = [node for node in ast.parse(path.read_text()).body if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
    body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)] + nodes
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(path), "exec"), module.__dict__)
    sys.modules[name] = module
    return module

common = {"Any": Any, "date": date, "func": func, "re": re, "MONTH_PATTERN": pattern}
capability = load_read_module("mcp_service.capability", pattern_source, {**common, "select": select, "case": case})
read_ops = load_read_module("mcp_service.read_ops", root / "legacy/mcp_service/read_ops.py", {**common, "BadMonth": capability.BadMonth})
legacy_package.capability, legacy_package.read_ops = capability, read_ops
source = root / "legacy/mcp_service/scenario_ops.py"
helpers = [node for node in ast.parse(source.read_text()).body if isinstance(node, ast.FunctionDef)
           and node.name in ("_filter_my_pay", "_latest_month_with_data", "_person_view", "_overview_summary")]
namespace = {**common, "guards": SimpleNamespace(GuardError=read_ops.BadParam)}
helper_tree = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)] + helpers, type_ignores=[])
exec(compile(ast.fix_missing_locations(helper_tree), str(source), "exec"), namespace)
engine = create_engine(url, connect_args={"options": "-c default_transaction_read_only=on"})
try:
    results = []
    with Session(engine) as db:
        for case in json.load(sys.stdin):
            code, month = case.get("personCode"), case.get("month")
            if case["tool"] == "visit_overview":
                view = case.get("view", "summary")
                if view == "months":
                    value = read_ops.list_months(db)
                elif view == "ranking":
                    value = read_ops.perf_ranking(db, month, case.get("limit", 10))
                else:
                    value = namespace["_overview_summary"](db, month)
            elif case["tool"] == "visit_person":
                value = namespace["_person_view"](db, case.get("person") or case.get("name"), month)
            elif case["tool"] == "visit_my_pay":
                value = my_ops.my_settlement(db, code)
                if month is not None:
                    value = namespace["_filter_my_pay"](value, month)
            elif case.get("view", "month") == "daily":
                month = month or my_ops._latest_month(db, code)
                value = my_ops.my_daily(db, code, month) if month else {
                    "month": None, "person_code": code, "daily": [], "summary": None,
                    "currency": "JPY", "hint": "该员工暂无绩效数据（合法结果，不是错误）",
                }
            else:
                value = my_ops.my_perf(db, code, month)
            results.append(value)
    print(json.dumps(results, ensure_ascii=False))
finally:
    engine.dispose()
