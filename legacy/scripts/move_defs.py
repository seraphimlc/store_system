# -*- coding: utf-8 -*-
"""按 AST 精确搬运模块级定义（函数/赋值）到另一个文件：先删后追加。

用法：`./.venv/bin/python scripts/move_defs.py <src> <dst> <name> [name...]`
比手工切片安全：用 ast 的 lineno/end_lineno 定位，且会拒绝搬运不存在的名字。
"""
import ast
import io
import sys


def _blocks(path, names):
    src = io.open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    lines = src.split("\n")
    out, missing = [], set(names)
    for node in tree.body:
        nm = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nm = node.name
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            nm = node.targets[0].id
        if nm in missing:
            start = node.lineno - 1
            while start > 0 and (lines[start - 1].startswith("@")
                                 or lines[start - 1].lstrip().startswith("#")):
                start -= 1
            end = node.end_lineno
            while end < len(lines) and lines[end].strip() == "":
                end += 1
            out.append((start, end, "\n".join(lines[start:end]).rstrip() + "\n"))
            missing.discard(nm)
    if missing:
        raise SystemExit("找不到定义：%s" % sorted(missing))
    return src, lines, sorted(out, key=lambda x: x[0])


def main():
    src_path, dst_path, names = sys.argv[1], sys.argv[2], sys.argv[3:]
    src, lines, blocks = _blocks(src_path, names)
    moved = "\n\n".join(b[2] for b in blocks)
    # 先把结果写进目标（不存在就创建），成功后再改源文件 —— 避免"改了一半崩掉"
    try:
        dst_text = io.open(dst_path, encoding="utf-8").read().rstrip()
    except FileNotFoundError:
        dst_text = "# -*- coding: utf-8 -*-"
    io.open(dst_path, "w", encoding="utf-8").write(dst_text + "\n\n\n" + moved)
    for start, end, _ in sorted(blocks, key=lambda x: x[0], reverse=True):
        del lines[start:end]
    io.open(src_path, "w", encoding="utf-8").write("\n".join(lines))
    print("搬运 %s → %s：%s（%d 行）" % (src_path, dst_path, names,
                                     len(moved.split("\n"))))


if __name__ == "__main__":
    main()
