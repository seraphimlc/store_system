# -*- coding: utf-8 -*-
"""半自动 i18n 包裹工具：把模板文本节点与 placeholder/title 属性中的中文
包进 t('..')（跳过已包裹、{{ }}、{% %} 与纯 JS 内容）。输出到同文件。
用法: python scripts/i18n_wrap.py app/templates/xxx.html [更多文件...]
"""
import re
import sys
from html.parser import HTMLParser

CN = re.compile(r'([\u4e00-\u9fff][\u4e00-\u9fff\w·（）()，。：:、/％%\-+円点点店月日#×÷ ]*)')
# 已包裹 t(' 或单引号字符串内的中文跳过
SKIP_PREFIX = ("t('", "{{ t('", "{{ t(\"", "value=\"t('")


class _P(HTMLParser):
    def __init__(self, src):
        super().__init__(convert_charrefs=True)
        self.src = src
        self.parts = []
        self.skip_until = None

    def handle_data(self, data):
        self.parts.append(("text", data))

    def handle_starttag(self, tag, attrs):
        self.parts.append(("tag", self.get_starttag_text() or ""))

    def handle_endtag(self, tag):
        self.parts.append(("tag", f"</{tag}>"))

    def handle_startendtag(self, tag, attrs):
        self.parts.append(("tag", self.get_starttag_text() or ""))


def wrap_text(src: str) -> str:
    """对文本节点中文包 t()。返回新 src。"""
    p = _P(src)
    p.feed(src)
    out = []
    for kind, chunk in p.parts:
        if kind == "text":
            # 不处理 script/style 内部
            out.append(chunk)
            continue
        out.append(chunk)
    # 重新拼接会丢失原始空白/属性，改用基于位置的替换
    return src


def wrap_src(src: str) -> str:
    """基于文本节点位置替换（保留原始标记不变）。"""
    # 保护 script/style 区域：不处理其中的中文
    protected = []

    def _protect(m):
        protected.append(m.group(0))
        return f"\x00PROTECT{len(protected)-1}\x00"

    src2 = re.sub(r'<(script|style)[^>]*>.*?</\1>', _protect, src, flags=re.S)
    p = _P(src2)
    p.feed(src2)
    out = []
    pos = 0
    for kind, chunk in p.parts:
        idx = src2.find(chunk, pos)
        if idx < 0:
            continue
        if kind == "text":
            out.append(src2[pos:idx])
            out.append(wrap_text_chunk(chunk))
        else:
            out.append(src2[pos:idx + len(chunk)])
        pos = idx + len(chunk)
    out.append(src2[pos:])
    joined = "".join(out)
    for i, blk in enumerate(protected):
        joined = joined.replace(f"\x00PROTECT{i}\x00", blk)
    return joined


def wrap_text_chunk(text: str) -> str:
    """把一个文本节点的中文片段包 t()。"""
    if not re.search(r'[\u4e00-\u9fff]', text):
        return text
    stripped = text.strip()
    # 纯空白/空
    if not stripped:
        return text
    # 已包裹或含 Jinja 表达式的整段：逐段替换
    def _rep(m):
        s = m.group(1).strip()
        if not s:
            return m.group(0)
        # 跳过已包裹
        pre = m.group(0)[:m.start(1)] if m.start(1) < 4 else ""
        if s.startswith("t('") or s.startswith('t("'):
            return m.group(0)
        return "{{ t('%s') }}" % s.replace("'", "\\'")
    return CN.sub(_rep, text)


def wrap_attrs(src: str) -> str:
    """placeholder/title 属性值中的中文包 t()。"""
    # placeholder="中文..." / title="中文..."
    def _attr(m):
        name, val = m.group(1), m.group(2)
        if not re.search(r'[\u4e00-\u9fff]', val):
            return m.group(0)
        if val.startswith("t(") or "{{" in val:
            return m.group(0)
        return f'{name}="{{{{ t(\'{val}\') }}}}"'
    src = re.sub(r'\b(placeholder|title)="([^"]*)"', _attr, src)
    return src


def main(files):
    for f in files:
        src = open(f, encoding="utf-8").read()
        new = wrap_attrs(wrap_src(src))
        if new != src:
            open(f, "w", encoding="utf-8").write(new)
            print("wrapped", f)
        else:
            print("unchanged", f)


if __name__ == "__main__":
    main(sys.argv[1:])
