"""Shape-first grid grouping uses real unions and rejects thin-waist territories."""
import pytest
from shapely import union_all
from shapely.geometry import MultiPolygon, box

from app.services import bd_grid_shapes as shapes, bd_zones as z


def dumbbell(scale=1):
    return union_all([box(0, 0, 1000*scale, 1000*scale),
                      box(1400*scale, 0, 2400*scale, 1000*scale),
                      box(1000*scale, 440*scale, 1400*scale, 560*scale)])


@pytest.mark.parametrize('scale', [1, 3])
def test_shape_gate_rejects_i_shape_even_when_convex_fill_and_aspect_pass(scale):
    geom = dumbbell(scale)
    quality = z._shape_metrics(geom)
    assert quality['solidity'] > .65 and quality['aspect'] < 3
    assert not shapes.acceptable(geom)


def test_shape_gate_ignores_lake_holes_but_rejects_deep_concavity_and_long_strips():
    lake = box(0, 0, 1000, 1000).difference(box(400, 400, 600, 600))
    assert shapes.acceptable(lake)
    assert not shapes.acceptable(box(0, 0, 4000, 500))
    u_shape = union_all([box(0, 0, 200, 1000), box(800, 0, 1000, 1000), box(0, 0, 1000, 200)])
    assert not shapes.acceptable(u_shape)


def test_grouping_keeps_coverage_instead_of_forcing_a_single_i_shaped_region():
    geoms = [box(0, 0, 1000, 1000), box(1400, 0, 2400, 1000), box(1000, 440, 1400, 560)]
    groups = shapes.group(geoms, [20, 20, 5], 1, 5e6)
    assert len(groups) > 1
    assert set().union(*(g['members'] for g in groups)) == {0, 1, 2}
    assert sum(g['count'] for g in groups) == 45
    assert all(shapes.acceptable(g['geom']) for g in groups if len(g['members']) > 1)
    assert union_all([g['geom'] for g in groups]).equals(union_all(geoms))


def test_grouping_recovers_multipart_grid_only_with_a_good_final_shape():
    geoms = [MultiPolygon([box(0, 0, 1000, 1000), box(2000, 0, 2100, 1000)]),
             box(1000, 0, 2000, 1000)]
    groups = shapes.group(geoms, [20, 20], 2, 3e6)
    assert len(groups) == 1 and groups[0]['members'] == {0, 1}
    assert shapes.acceptable(groups[0]['geom'])
    assert groups[0]['count'] == 40


@pytest.mark.parametrize('limits', [dict(max_area=1e6), dict(max_area=3e6,max_members=1),
                                  dict(max_area=3e6,max_stores=39)])
def test_shape_repairs_respect_hard_limits(limits):
    geoms = [MultiPolygon([box(0, 0, 1000, 1000), box(2000, 0, 2100, 1000)]),
             box(1000, 0, 2000, 1000)]
    groups = shapes.group(geoms, [20, 20], 2, **limits)
    assert [g['members'] for g in groups] == [{0}, {1}]


@pytest.mark.parametrize('other', [box(1000.01, 0, 2000, 1000), box(1000, 1000, 2000, 2000)])
def test_shape_grouping_never_connects_a_gap_or_corner(other):
    groups = shapes.group([box(0, 0, 1000, 1000), other], [20, 20], 1, 3e6)
    assert len(groups) == 2


def test_shape_gate_rejects_long_thin_spur_with_high_convex_fill():
    geom = union_all([box(0, 0, 1000, 1000), box(1000, 475, 1500, 525)])
    assert z._shape_metrics(geom)['solidity'] > .75
    assert not shapes.acceptable(geom)


def test_shape_partition_keeps_coverage_when_one_long_strip_is_rejected():
    geoms = [box(i * 1000, 0, (i + 1) * 1000, 1000) for i in range(4)]
    # All source grids are already compact; conservative grouping remains exact.
    groups = shapes.group(geoms, [5] * 4, 1, 4e6)
    assert sum(len(g['members']) for g in groups) == 4
    assert all(shapes.acceptable(g['geom']) for g in groups)
    assert union_all([g['geom'] for g in groups]).equals(box(0, 0, 4000, 1000))


def test_edge_grid_transfer_recovers_a_long_leftover_without_breaking_the_donor():
    geoms = [box(0, 0, 4000, 1000), box(0, 1000, 4000, 2000),
             union_all([box(0, 2000, 8000, 5000), box(4000, 1000, 8000, 2000)])]
    groups = [{'geom': geoms[0], 'members': {0}, 'count': 5},
              {'geom': union_all(geoms[1:]), 'members': {1, 2}, 'count': 10}]
    result = shapes._transfer(groups, geoms, [5, 5, 5], 34e6)
    assert {frozenset(g['members']) for g in result} == {frozenset({0, 1}), frozenset({2})}
    assert all(shapes.acceptable(g['geom']) for g in result)
    assert sum(g['count'] for g in result) == 15
    assert union_all([g['geom'] for g in result]).equals(union_all(geoms))
    assert groups[0]['members'] == {0} and groups[1]['members'] == {1, 2}


@pytest.mark.parametrize('parts', [
    [box(0, 0, 1000, 1000), box(1000, 0, 2000, 1000)],
    [box(0, 0, 1000, 1000), box(500, 0, 1500, 1000)],
])
def test_fast_union_matches_general_union_for_coverage_and_overlapping_inputs(parts):
    assert shapes._join(parts).equals(union_all(parts, grid_size=.001))


def test_blocked_shape_repair_is_retried_when_its_supporting_neighbors_change():
    geoms = [
        MultiPolygon([box(0, 0, 1000, 3000), box(5000, 0, 6000, 3000)]),
        box(1000, 0, 2000, 3000),
        MultiPolygon([box(2000, 0, 3000, 1500), box(2000, 2000, 3000, 3000)]),
        box(3000, 0, 4000, 3000), box(4000, 0, 5000, 3000),
    ]
    groups = [{'geom': g, 'members': {i}, 'count': 5} for i, g in enumerate(geoms)]
    result = shapes._repair(groups, 25e6)
    assert len(result) == 1
    assert result[0]['members'] == set(range(5)) and result[0]['count'] == 25
    assert shapes.acceptable(result[0]['geom'])
    assert result[0]['geom'].equals(union_all(geoms))
