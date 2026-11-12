"""手机端顶栏标题**由页面决定**：页面给了标题就让品牌名让位；PC 端不受影响。

约定（改模板前先看这里）：
- base.html 顶栏 = 品牌名 `.brand.topbar-brand` + `.mobile-navigation-title`（装页面标题）。
- 页面标题元素必须带 `.dispatch-title` 或 `.work-nav-title`，手机端才认得出「有标题」。
- 规则写在 app.css 的 `@media (max-width: 860px)` 里 → PC 端（>860px）行为不变。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / 'app/static/app.css').read_text(encoding='utf-8')
BASE = (ROOT / 'app/templates/base.html').read_text(encoding='utf-8')
MOBILE = re.search(r'@media \(max-width: 860px\) \{(.*?)\n\}', CSS, re.S).group(1)


def test_mobile_hides_brand_only_when_page_provides_title():
    assert '.topbar-brand' in MOBILE
    assert ':has(.mobile-navigation-title .dispatch-title)' in MOBILE
    assert ':has(.mobile-navigation-title .work-nav-title)' in MOBILE
    assert re.search(r'\.topbar-brand\s*\{[^}]*display:\s*none', MOBILE)


def test_brand_markup_is_before_the_page_title_slot():
    assert 'class="brand topbar-brand"' in BASE
    assert BASE.index('topbar-brand') < BASE.index('mobile-navigation-title')


def test_desktop_rule_does_not_hide_brand():
    """品牌名的隐藏规则只能出现在手机端媒体查询里。"""
    desktop = CSS.split('@media (max-width: 860px)')[0]
    assert 'topbar-brand' not in desktop


def test_every_page_title_uses_a_known_hook_class():
    """页面标题必须带 .dispatch-title 或 .work-nav-title，否则手机端认不出标题。"""
    for path in sorted((ROOT / 'app/templates').glob('*.html')):
        text = path.read_text(encoding='utf-8')
        match = re.search(r'\{%\s*block navigation_title\s*%\}(.*?)\{%\s*endblock\s*%\}', text, re.S)
        if not match or not match.group(1).strip():
            continue
        body = match.group(1)
        assert ('dispatch-title' in body) or ('work-nav-title' in body), path.name
