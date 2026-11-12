"""Run old preprocessing main using fixed source files and a temporary output."""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import zipfile
payload=json.load(sys.stdin)
spec=importlib.util.spec_from_file_location('legacy_fetch_rail',os.path.join(os.path.dirname(__file__),'bd_fetch_rail.py'))
legacy=importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
with tempfile.TemporaryDirectory(prefix='rail-preprocess-oracle-') as directory:
    archive=os.path.join(directory,'rail.zip')
    with zipfile.ZipFile(archive,'w') as output:output.writestr('UTF-8/N02-22_Station.geojson',json.dumps(payload['stations']))
    borders=os.path.join(directory,'borders.json')
    with open(borders,'w',encoding='utf8') as output:json.dump(payload['borders'],output)
    legacy.fetch=lambda url,dest,refresh=False: archive if url==legacy.BASE else borders
    legacy.OUT=os.path.join(directory,'result.json')
    sys.argv=['bd_fetch_rail.py']
    with contextlib.redirect_stdout(io.StringIO()):legacy.main()
    with open(legacy.OUT,encoding='utf8') as result:print(result.read())
