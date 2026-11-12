"""Runs existing geometry main on an in-memory session. No real DB writes."""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
payload = json.load(sys.stdin)
spec = importlib.util.spec_from_file_location('legacy_geometry', os.path.join(os.path.dirname(__file__), 'bd_seq_from_geometry.py'))
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
class Rows:
    def __init__(self, values): self.values = values
    def all(self): return self.values
class Memory:
    def execute(self, statement, params=None):
        query = str(statement)
        if query.startswith('SELECT id, operator, name'):
            return Rows([(r['id'], r['operator'], r['name'], r.get('n_station', 0)) for r in payload['lines']])
        if query.startswith('SELECT DISTINCT line_id'):
            return Rows([(r['line_id'],) for r in payload['stations'] if (r.get('seq_src') or '').startswith('osm:') and r['line_id'] is not None])
        if query.startswith('SELECT id, name, lat, lon'):
            return Rows([(r['id'], r['name'], r['lat'], r['lon']) for r in payload['stations'] if r['line_id'] == params['i'] and r['lat'] is not None])
        raise RuntimeError('Unexpected legacy query')
    def close(self): pass
import app.db
app.db.SessionLocal = Memory
with tempfile.TemporaryDirectory(prefix='rail-geometry-oracle-') as directory:
    source = os.path.join(directory, 'input.geojson')
    with open(source, 'w', encoding='utf8') as out:
        json.dump({'type':'FeatureCollection', 'features':[{'type':'Feature', 'properties':{'N02_004':r['operator'], 'N02_003':r['line']}, 'geometry':{'type':'LineString', 'coordinates':r['coordinates']}} for r in payload['sections']]}, out)
    legacy.OUT = os.path.join(directory, 'output.json')
    sys.argv = ['bd_seq_from_geometry.py', '--geojson', source, '--apply'] + (['--all'] if payload.get('all') else [])
    with contextlib.redirect_stdout(io.StringIO()): legacy.main()
    with open(legacy.OUT, encoding='utf8') as result: data=json.load(result)
print(json.dumps(data['lines'], ensure_ascii=True))
