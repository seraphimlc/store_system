from types import SimpleNamespace

from shapely.geometry import box, mapping

from scripts.bd_refresh_tokyo_names import make_region_rows


def test_region_names_summarize_up_to_three_grid_labels_as_one_line():
    region = SimpleNamespace(id=1, release_id=1, geometry=mapping(box(0, 0, 5, 5)), name='old')
    grids = [SimpleNamespace(id=i, zone_id=1, release_id=1, geometry=mapping(box(i, 0, i + .5, .5)))
             for i in range(1, 6)]

    rows = make_region_rows([region], grids, {1: '青梅', 2: '青梅', 3: '河辺', 4: '秋川', 5: '立川'})

    assert rows[0]['point_names'] == ['青梅', '河辺', '秋川']
    assert rows[0]['name'] == '青梅&河辺&秋川'
    assert '\n' not in rows[0]['name'] and len(rows[0]['point_names']) <= 3


def test_unassigned_archived_region_uses_intersecting_current_grids():
    region = SimpleNamespace(id=2, release_id=1, geometry=mapping(box(0, 0, 2, 2)), name='archived')
    grids = [SimpleNamespace(id=10, zone_id=None, release_id=1, geometry=mapping(box(1, 1, 3, 3)))]

    rows = make_region_rows([region], grids, {10: '八王子'})

    assert rows[0]['name'] == '八王子'
    assert rows[0]['member_grid_ids'] == [10]
    assert 'unchanged_reason' not in rows[0]
