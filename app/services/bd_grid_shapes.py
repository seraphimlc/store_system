"""Shape-first partitioning of indivisible work grids; final boundaries remain exact unions."""
import math

from shapely import coverage_union_all, set_precision, union_all
from shapely.errors import GEOSException
from shapely.geometry import Polygon

from app.services import bd_zones as z


def _join(parts, *, grid_size=.001):
    """Fast path for valid coverage; overlapping/non-noded inputs use general union."""
    try:
        result = coverage_union_all(parts)
        area = sum(p.area for p in parts)
        if result.is_valid and abs(result.area - area) <= max(.01, area * 1e-9):
            return result
    except GEOSException:
        pass
    return union_all(parts, grid_size=grid_size)


def acceptable(geom):
    if geom.geom_type != 'Polygon' or not geom.is_valid or geom.is_empty:
        return False
    quality = z._shape_metrics(geom)
    if quality['solidity'] < .75 - z.SHAPE_TOLERANCE or quality['aspect'] > 3 + z.SHAPE_TOLERANCE:
        return False
    # Detect substantial lobes joined by a thin waist, including I/H-shaped areas.
    # Scale with territory size: a fixed 150 m test misses larger rural necks.
    shell = Polygon(geom.exterior)
    erosion = min(300., math.sqrt(shell.area) * .10)
    core = shell.buffer(-erosion)
    lobes = [p for p in z.polygons(core) if p.area > shell.area * .04]
    if len(lobes) > 1 or core.is_empty:
        return False
    # A long thin spur can have tiny area and escape both the hull and waist tests.
    # Compare against a morphological opening without changing the output boundary.
    opened = core.buffer(erosion)
    return shell.hausdorff_distance(opened) <= math.sqrt(shell.area) * .25


def _context_token(key, graph, active, owner=None):
    """A repair can use only three edge hops; unchanged inputs need no retry."""
    nearby = {key} if owner is None else set(active[key]['members'])
    for _ in range(3):
        nearby |= set().union(*(graph[i] for i in nearby))
    keys = nearby if owner is None else {owner[i] for i in nearby}
    return tuple((i, frozenset(active[i]['members'])) for i in sorted(keys))


def _repair(groups, max_area, *, max_stores=None, max_members=None):
    """Accept a whole-neighbor repair only when its final union passes every shape gate."""
    active = {i: {**g, 'members': set(g['members'])} for i, g in enumerate(groups)}
    graph = z.neighbours([active[k]['geom'] for k in sorted(active)])
    unacceptable = {k for k in active if not acceptable(active[k]['geom'])}
    failed = {}
    while unacceptable and len(active) > 1:
        did_merge = False
        for key in sorted(unacceptable, key=lambda k: min(active[k]['members'])):
            token = _context_token(key, graph, active)
            if failed.get(key) == token:
                continue
            # Look at up to three supporting neighbors. Failed proposals never
            # consume a healthy group. Only the exact union may become a territory.
            pending = [frozenset({key})]
            seen = set(pending)
            options = []
            for depth in range(3):
                following = []
                for selected in pending:
                    candidates = set().union(*(graph[i] for i in selected)) - selected
                    for other in sorted(candidates):
                        chosen = selected | {other}
                        if chosen in seen:
                            continue
                        seen.add(chosen)
                        parts = [active[i] for i in sorted(chosen)]
                        members = set().union(*(g['members'] for g in parts))
                        count = sum(g['count'] for g in parts)
                        area = sum(g['geom'].area for g in parts)
                        if (area > max_area + 1
                                or max_stores is not None and count > max_stores
                                or max_members is not None and len(members) > max_members):
                            continue
                        geom = _join([g['geom'] for g in parts])
                        if acceptable(geom):
                            q = z._shape_metrics(geom)
                            score = (len(chosen), -q['compactness'], -q['solidity'], min(members))
                            options.append((score, chosen, geom, members, count))
                        elif geom.is_valid and geom.geom_type in ('Polygon', 'MultiPolygon'):
                            following.append(chosen)
                if options:
                    break
                pending = sorted(following, key=lambda p: tuple(sorted(p)))[:32]
                if not pending:
                    break
            if options:
                _, chosen, geom, members, count = min(options, key=lambda item: item[0])
                adjacent = set().union(*(graph[i] for i in chosen)) - chosen
                active[key] = {'geom': geom, 'members': members, 'count': count}
                for i in adjacent:
                    graph[i].difference_update(chosen)
                    graph[i].add(key)
                for i in chosen - {key}:
                    del active[i]
                    del graph[i]
                graph[key] = adjacent
                unacceptable.difference_update(chosen)
                did_merge = True
                break
            failed[key] = token
        if not did_merge:
            break
    return list(active.values())


def _transfer(groups, geoms, counts, max_area, *, max_stores=None, max_members=None):
    """Repair a leftover using whole edge grids, keeping every donor territory sound."""
    active = {i: {**g, 'members': set(g['members'])} for i, g in enumerate(groups)}
    bad = {k for k in active if not acceptable(active[k]['geom'])}
    if not bad:
        return list(active.values())
    graph = z.neighbours(geoms)
    failed = {}
    while True:
        owner = {i: key for key, g in active.items() for i in g['members']}
        changed = False
        for key in sorted(bad, key=lambda k: min(active[k]['members'])):
            token = _context_token(key, graph, active, owner)
            if failed.get(key) == token:
                continue
            source = active[key]
            pending, seen, options = [frozenset()], {frozenset()}, []
            for depth in range(3):
                following = []
                for added in pending:
                    part = source['members'] | added
                    frontier = set().union(*(graph[i] for i in part)) - part
                    for i in sorted(frontier):
                        selected = added | {i}
                        if selected in seen:
                            continue
                        seen.add(selected)
                        members = source['members'] | selected
                        count = sum(counts[j] for j in members)
                        if (sum(geoms[j].area for j in members) > max_area + 1
                                or max_stores is not None and count > max_stores
                                or max_members is not None and len(members) > max_members):
                            continue
                        combined = _join([geoms[j] for j in sorted(members)])
                        if not acceptable(combined):
                            following.append(selected)
                            continue
                        donors = {}
                        for donor in {owner[j] for j in selected}:
                            remaining = active[donor]['members'] - selected
                            if remaining:
                                geom = _join([geoms[j] for j in sorted(remaining)])
                                if not acceptable(geom):
                                    break
                                donors[donor] = {'geom': geom, 'members': remaining,
                                                'count': sum(counts[j] for j in remaining)}
                            else:
                                donors[donor] = None
                        else:
                            quality = z._shape_metrics(combined)
                            score = (len(selected), -quality['compactness'], tuple(sorted(selected)))
                            options.append((score, combined, members, count, donors))
                if options:
                    break
                pending = sorted(following, key=lambda part: tuple(sorted(part)))[:32]
                if not pending:
                    break
            if options:
                _, geom, members, count, donors = min(options, key=lambda item: item[0])
                active[key] = {'geom': geom, 'members': members, 'count': count}
                for donor, replacement in donors.items():
                    if replacement is None:
                        del active[donor]
                    else:
                        active[donor] = replacement
                bad.difference_update({key, *donors})
                changed = True
                break
            failed[key] = token
        if not changed:
            return list(active.values())


def group(geoms, counts, target_count, max_area, *, max_stores=None, max_members=None):
    """Shape is mandatory; requested workload/count is a best-effort target."""
    # Use the same millimeter precision as the general union before coverage union.
    geoms = [set_precision(g, .001) if g.is_valid else g for g in geoms]
    groups = z._group(geoms, counts, target_count, max_area,
                      merge_guard=acceptable, max_stores=max_stores, max_members=max_members,
                      merge_geometry=_join)
    groups = _repair(groups, max_area, max_stores=max_stores, max_members=max_members)
    return _transfer(groups, geoms, counts, max_area, max_stores=max_stores, max_members=max_members)
