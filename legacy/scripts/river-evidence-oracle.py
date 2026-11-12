"""Read-only oracle for OSM river-face decoding, including fragmented relations and holes."""
import json
import os
import sys
from pathlib import Path
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shapely.geometry import mapping, GeometryCollection
from shapely import union_all
from app.services import bd_zones as z
from app.services.bd_zone_rules import load_river_geometry
from scripts.bd_collect_zones import exclusions
if len(sys.argv)>1:
    result=load_river_geometry(Path(sys.argv[1]))
else:
    elements={}
    for element in json.load(sys.stdin):
        tags=element.get('tags',{})
        if tags.get('water')=='river' or tags.get('waterway')=='riverbank':
            elements[(element['type'],element['id'])]=element
    result=union_all([z.projected(g) for g in exclusions(elements.values())],grid_size=.001)
print(json.dumps(dict(geometry=mapping(result),area=result.area)))
