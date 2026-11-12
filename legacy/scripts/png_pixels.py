# -*- coding: utf-8 -*-
"""看 PNG 的像素（无第三方库，只用 zlib + struct）——用来客观判断"样式到底上没上"。

我这个模型读不了图片，但可以采像素：例如管理端左侧菜单应该是**深蓝底**
（#0d1b34 侧栏 / #10203f 顶栏），白色内容区在右侧。取几个点比颜色就能判断。

    ./.venv/bin/python scripts/png_pixels.py /tmp/nav_dash.png x,y x,y ...
    ./.venv/bin/python scripts/png_pixels.py /tmp/nav_dash.png --strip 100
"""
import struct
import sys
import zlib


def read_png(path):
    """返回 (width, height, get(x,y)->(r,g,b))，支持 8bit RGB/RGBA/灰度。"""
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "不是 PNG"
    pos, idat, pal, trns = 8, b"", None, None
    w = h = bitd = ctype = None
    while pos < len(data):
        (ln,) = struct.unpack(">I", data[pos:pos + 4])
        typ = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, bitd, ctype = struct.unpack(">IIBB", body[:10])
        elif typ == b"IDAT":
            idat += body
        elif typ == b"PLTE":
            pal = body
        elif typ == b"tRNS":
            trns = body
        elif typ == b"IEND":
            break
        pos += 12 + ln
    assert bitd == 8, "只支持 8bit（实际 %s）" % bitd
    nch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(idat)
    stride = w * nch
    rows, prev, p = [], bytearray(stride), 0
    for _ in range(h):
        f = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        for i in range(stride):                      # 反过滤
            a = line[i - nch] if i >= nch else 0
            b = prev[i]
            c = prev[i - nch] if i >= nch else 0
            x = line[i]
            if f == 1:
                x += a
            elif f == 2:
                x += b
            elif f == 3:
                x += (a + b) // 2
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                x += a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
            line[i] = x & 0xFF
        rows.append(bytes(line))
        prev = line

    def get(x, y):
        row = rows[y]
        i = x * nch
        if ctype == 3:
            idx = row[i]
            return tuple(pal[idx * 3:idx * 3 + 3])
        if nch >= 3:
            return (row[i], row[i + 1], row[i + 2])
        return (row[i], row[i], row[i])

    return w, h, get


def hexs(c):
    return "#%02x%02x%02x" % c


def main():
    path = sys.argv[1]
    w, h, get = read_png(path)
    print("尺寸: %dx%d" % (w, h))
    if "--strip" in sys.argv:
        x = int(sys.argv[sys.argv.index("--strip") + 1])
        print("x=%d 这一列的颜色分布（每 20 行取一个）:" % x)
        for y in range(0, h, 20):
            print("  y=%-4d %s" % (y, hexs(get(x, y))))
        return 0
    for arg in sys.argv[2:]:
        x, y = (int(v) for v in arg.split(","))
        print("  (%4d,%4d) = %s" % (x, y, hexs(get(x, y))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
