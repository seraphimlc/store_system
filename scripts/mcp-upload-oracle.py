"""Legacy file classification/estimation on test-created workbooks only."""
import json
from pathlib import Path
import sys
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "legacy"))
from mcp_service import file_kind
out = []
for case in json.load(sys.stdin):
    path = Path(case["path"]).resolve()
    if not path.parent.name.startswith("mcp-upload-"):
        raise RuntimeError("Only isolated synthetic workbooks are accepted")
    content = path.read_bytes()
    info = file_kind.detect_kind(content)
    forced = file_kind.normalize_kind(case.get("kind"))
    kind = forced or info["kind"]
    reason = "由调用方指定（kind=%s）" % forced if forced else info["reason"]
    out.append({"detected": {"kind": kind, "reason": reason},
                "month": case.get("month") or file_kind.infer_month(content, kind),
                **file_kind.estimate(content, kind)})
print(json.dumps(out, ensure_ascii=False))
