"""Sample points and pin filtering for grid place names. No network."""
from shapely.geometry import Point, box

from app.services import bd_grid_places as places


def test_small_grid_uses_one_interior_point():
    poly = box(0, 0, 800, 600)
    points = places.sample_points(poly, 1800)
    assert len(points) == 1
    assert poly.covers(points[0])
    radius = places.circle_radius(poly, 1800)
    assert 500 <= radius < 1800


def test_large_grid_uses_several_points_inside_the_polygon():
    poly = box(0, 0, 8000, 5000)
    points = places.sample_points(poly, 1800)
    assert len(points) > 1
    assert all(poly.covers(point) for point in points)
    assert poly.representative_point().distance(points[0]) < 1


def test_boundary_pin_is_kept_and_distant_pin_is_dropped():
    poly = box(0, 0, 1000, 1000)
    # keep_pins transforms lon/lat, so test the buffer rule in metric space
    # through a local stand-in: 20m outside is in, 200m outside is out.
    near = Point(-20, 500)
    far = Point(-200, 500)
    gate = poly.buffer(places.BOUNDARY_M)
    assert gate.covers(near)
    assert not gate.covers(far)
    assert gate.covers(Point(500, 500))


def test_scene_filter_drops_offices_monuments_and_child_places():
    kept = places.filter_scenes([
        '東京薬科大学', '東京薬科大学 庶務課', '東京薬科大学 史料館',
        'シャポー船橋', 'シャポー船橋 南館', '林野庁殉職者慰霊碑', '高尾山'])
    assert kept == ['東京薬科大学', 'シャポー船橋', '高尾山']


def test_grid_label_uses_nearest_station_and_keeps_town_without_one():
    assert places.grid_label('東京都調布市仙川町・G17442', ['仙川駅'], []) == '東京都調布市仙川駅'
    assert places.grid_label('東京都渋谷区神宮前・G3166', ['表参道駅'], []) == '東京都渋谷区表参道駅'
    assert places.municipality_of('東京都練馬区西大泉') == '東京都練馬区'
    assert places.grid_label('東京都青梅市柚木町・G17257', [], ['天狗岩']) == '東京都青梅市柚木町'
    assert places.grid_label('東京都26市・西部・G17261', [], ['高尾山']) == '高尾山'
    assert places.grid_label('東京都八王子市高尾町・G1', ['高尾'], []) == '東京都八王子市高尾駅'


def test_region_label_uses_a_station_in_the_regions_own_city():
    assert places.region_label([
        {'label': '東京都立川市立川駅', 'stores': 600},
        {'label': '東京都立川市柴崎町', 'stores': 100},
        {'label': '東京都昭島市中神駅', 'stores': 50},
    ], '東京都立川市柴崎町・R1') == '東京都立川市立川駅'
    assert places.region_label([
        {'label': '東京都青梅市河辺町', 'stores': 40},
        {'label': '東京都羽村市羽村駅', 'stores': 80},
        {'label': '東京都羽村市羽村駅', 'stores': 80},
    ], '東京都青梅市河辺町・R2') is None
    assert places.region_label([
        {'label': '東京都渋谷区表参道駅', 'stores': 400},
        {'label': '東京都渋谷区表参道駅', 'stores': 100},
        {'label': '東京都渋谷区神宮前', 'stores': 50},
    ], '东京23区·片区001') == '東京都渋谷区表参道駅'
    assert places.region_label([
        {'label': '東京都渋谷区表参道駅', 'stores': 100},
        {'label': '東京都港区青山', 'stores': 100},
    ], '东京23区·片区002') is None


def test_japanese_name_keeps_ja_and_drops_other_languages():
    assert places.japanese_name('表参道ヒルズ', 'ja') == '表参道ヒルズ'
    assert places.japanese_name('오모테산도 힐즈', 'ko') is None
    assert places.japanese_name('WITH HARAJUKU', '') is None
    assert places.japanese_name('next 船橋店', 'ja') == 'next 船橋店'
    assert places.japanese_name('  五日市駅  ', 'ja') == '五日市駅'
