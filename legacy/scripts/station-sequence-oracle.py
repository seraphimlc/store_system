"""Execute the existing sequence script against an in-memory DB adapter; no DB writes."""
import contextlib
import copy
import importlib.util
import io
import json
import os
import sys
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
payload = json.load(sys.stdin)
spec = importlib.util.spec_from_file_location('legacy_fill_seq', os.path.join(os.path.dirname(__file__), 'bd_fill_seq.py'))
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
class Rows:
    def __init__(self, rows): self.rows = rows
    def all(self): return self.rows
    def scalar(self): return self.rows[0][0] if self.rows else None
class Memory:
    def __init__(self): self.plan = {}
    def execute(self, statement, params=None):
        query = str(statement)
        if query.startswith('SELECT id, operator_short, name'):
            return Rows([(line['id'], line.get('operator_short', ''), line['name']) for line in payload['lines']])
        if query.startswith('SELECT id, name, lat, lon'):
            return Rows([(row['id'], row['name'], row['lat'], row['lon']) for row in payload['stations'] if row['line_id'] == params['i']])
        if query.startswith('SELECT COUNT(*)'): return Rows([(len(payload['stations']),)])
        if query.startswith('UPDATE bd_station SET seq=:s'):
            self.plan[params['i']] = {'stationId': params['i'], 'seq': params['s'], 'km': params['k'], 'source': params['src']}
            return Rows([])
        if query.startswith('UPDATE bd_station SET seq=NULL'): return Rows([])
        raise RuntimeError('Unexpected legacy query')
    def commit(self): pass
    def close(self): pass
memory = Memory()
import app.db
app.db.SessionLocal = lambda: memory
sources = payload['sources']
def load_routes():
    routes = [copy.deepcopy(row) for row in sources['osm']['routes'] if row['n_stop'] >= 2]
    for route in routes:
        km, previous = 0, None
        for stop in route['stops']:
            if previous: km += legacy.hav(previous['lat'], previous['lon'], stop['lat'], stop['lon']) / 1000
            stop['km'] = km
            previous = stop
    return sources['osm'], routes
legacy.load_routes = load_routes
with tempfile.TemporaryDirectory(prefix='sequence-oracle-') as directory:
    for name, field in [('geom', 'geometry'), ('manual', 'manual')]:
        filename = os.path.join(directory, name + '.json')
        with open(filename, 'w', encoding='utf8') as out: json.dump(sources[field], out)
        if name == 'geom': legacy.SRC_GEOM = filename
        else: legacy.SRC_MANUAL = filename
    sys.argv = ['bd_fill_seq.py', '--apply']
    with contextlib.redirect_stdout(io.StringIO()): legacy.main()
print(json.dumps(sorted(memory.plan.values(), key=lambda row: row['stationId']), ensure_ascii=True))
