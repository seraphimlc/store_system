"""Development-only legacy rebuild oracle, restricted to disposable local P4 clone."""
import contextlib
import io
import json
import os
import sys
from app.db import SessionLocal
from app.services.flow import rebuild_month
from app.services import perf

if '/store_settle_refactor_p4_rebuild_' not in os.environ.get('DATABASE_URL', ''):
    raise RuntimeError('Disposable rebuild clone required')
session = SessionLocal()
try:
    perf.warm_config(session, sys.argv[1])
    with contextlib.redirect_stdout(io.StringIO()):
        result = rebuild_month(session, sys.argv[1])
    print(json.dumps({'ok': bool(result.get('ok')), 'synced': result.get('synced'),
                      'effectiveBonus': list(perf.bonus_params(sys.argv[1])),
                      'formalAfter': result.get('formal_after'), 'pointsAfter': result.get('points_after')}))
finally:
    session.close()
