"""Python 版本兼容性断言 — 一次性脚本 (可重复跑)。

背景 (2026-10-27):
    Windows 上按 build.bat 构建后运行即崩:
        NameError: name '_CellInfo' is not defined
    根因: `def _cell_at(...) -> _CellInfo | None:` 的**返回类型注解**引用了
    后面才定义的类, 而 `class _CellInfo` 定义在 `VideoGridView` **之后**。

    Python ≤ 3.13 在**函数定义时**就求值注解 → 那时 `_CellInfo` 还不存在 →
    **导入期就 NameError**。Python 3.14 (PEP 649) 改成惰性求值 → 开发机上
    完全测不出来。

    同类的还有 `row._cell_info: dict[str, _CellInfo] = {...}` —— 带注解的
    属性赋值, 注解同样在运行时求值 (PEP 526)。

本脚本查两件事:
    A. 没有任何注解引用「后面才定义的名字」(旧版 Python 会 NameError)
    B. 源码实际要求的最低 Python 版本 ≤ build.bat 声称的版本

2026-10-29: Tk 版整体删除后, 注解都搬到了 `video_core.py` / `video_qt.py`
(两个文件加起来 2700+ 行, 比当年那个 Tk 文件更容易踩前向引用), 所以把扫描
目标改成这两个文件 —— 原脚本扫的是当时的 `video_checker.py`。

跑法: python docs/superpowers/specs/2026-10-27-python-compat-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import ast
import builtins
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
# 注解现在都在这两个文件里 (Tk 版删掉之后从 video_checker.py 搬过来的)
SRCS = [os.path.join(_ROOT, 'video_core.py'),
        os.path.join(_ROOT, 'video_qt.py')]
BUILD = os.path.join(_ROOT, 'build.bat')

_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "✓" if condition else "✗"
    line = f"  {mark} {name}" + (f" — {detail}" if detail else "")
    print(line)
    if not condition:
        _failures.append(line)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def _target_names(t):
    if isinstance(t, ast.Name):
        return {t.id}
    if isinstance(t, (ast.Tuple, ast.List)):
        out = set()
        for e in t.elts:
            out |= _target_names(e)
        return out
    return set()


def _defines(node):
    """这个语句定义了哪些名字。"""
    names = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names.add(node.name)
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            names |= _target_names(t)
    elif isinstance(node, ast.AnnAssign):
        names |= _target_names(node.target)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for a in node.names:
            names.add((a.asname or a.name).split('.')[0])
    elif isinstance(node, (ast.If, ast.Try, ast.For, ast.While, ast.With)):
        for f in ast.iter_fields(node):
            v = f[1]
            for sub in (v if isinstance(v, list) else [v]):
                if isinstance(sub, ast.stmt):
                    names |= _defines(sub)
    return names


def _annotations(node):
    """这个语句上所有注解。"""
    out = []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = node.args
        out += [x.annotation for x in
                (a.posonlyargs + a.args + a.kwonlyargs) if x.annotation]
        if a.vararg and a.vararg.annotation:
            out.append(a.vararg.annotation)
        if a.kwarg and a.kwarg.annotation:
            out.append(a.kwarg.annotation)
        if node.returns:
            out.append(node.returns)
    elif isinstance(node, ast.AnnAssign):
        out.append(node.annotation)
    return out


def _annotation_names(node):
    out = set()
    for f in _annotations(node):
        for n in ast.walk(f):
            if isinstance(n, ast.Name):
                out.add(n.id)
    return out


def find_forward_refs(tree):
    """返回 [(行号, 位置, 缺失的名字)]。"""
    problems = []
    known_builtins = set(dir(builtins))

    def walk_body(body, visible, where):
        for stmt in body:
            missing = {n for n in _annotation_names(stmt)
                       if n not in visible and n not in known_builtins}
            if missing:
                problems.append((stmt.lineno, where, sorted(missing)))
            visible = visible | _defines(stmt)
            if isinstance(stmt, ast.ClassDef):
                walk_body(stmt.body, visible | {stmt.name}, f"class {stmt.name}")
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk_body(stmt.body, visible, f"def {stmt.name}")
            elif isinstance(stmt, ast.If):
                walk_body(stmt.body, visible, f"if@{stmt.lineno}")
                walk_body(stmt.orelse, visible, f"else@{stmt.lineno}")
            elif isinstance(stmt, ast.Try):
                walk_body(stmt.body, visible, f"try@{stmt.lineno}")
                for h in stmt.handlers:
                    walk_body(h.body, visible, f"except@{stmt.lineno}")
                walk_body(stmt.orelse, visible, f"tryelse@{stmt.lineno}")
                walk_body(stmt.finalbody, visible, f"finally@{stmt.lineno}")

    walk_body(tree.body, set(), 'module')
    return problems


def required_python(tree):
    """源码实际要求的最低 Python 版本 (major, minor)。"""
    need = (3, 8)
    for node in ast.walk(tree):
        anns = []
        if isinstance(node, ast.AnnAssign):
            anns.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            anns += [x.annotation for x in
                     (a.posonlyargs + a.args + a.kwonlyargs) if x.annotation]
            if node.returns:
                anns.append(node.returns)
        for ann in anns:
            for n in ast.walk(ann):
                # `X | Y` 联合类型 → 3.10+
                if isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr):
                    need = max(need, (3, 10))
                # `list[str]` / `dict[str, X]` → 3.9+
                if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name):
                    if n.value.id in ('list', 'dict', 'tuple', 'set', 'frozenset',
                                      'type'):
                        need = max(need, (3, 9))
    return need


def main() -> int:
    trees = {}
    for src in SRCS:
        with open(src, encoding='utf-8', newline='') as f:
            trees[src] = ast.parse(f.read(), filename=src)

    section("A. 注解不得引用后面才定义的名字")
    print("  (Python ≤ 3.13 在定义时就求值注解 → 前向引用 = 导入期 NameError)")
    for src, tree in trees.items():
        name = os.path.basename(src)
        problems = find_forward_refs(tree)
        check(f"{name}: 没有前向引用的注解", not problems,
              f"{len(problems)} 处: " + "; ".join(
                  f"行{l}:{m}" for l, _w, m in problems) if problems else "")
        for lineno, where, missing in problems:
            print(f"      行 {lineno} ({where}): {missing}")

    section("B. build.bat 声称的 Python 版本要够用")
    need = max(required_python(t) for t in trees.values())
    need_s = f"{need[0]}.{need[1]}"
    with open(BUILD, encoding='utf-8', newline='') as f:
        bat = f.read()
    m = re.search(r'Python\s+(\d+)\.(\d+)\+', bat)
    check("build.bat 里写了 Python 版本要求", m is not None)
    if m:
        claimed = (int(m.group(1)), int(m.group(2)))
        claimed_s = f"{claimed[0]}.{claimed[1]}"
        check(f"声称的 {claimed_s} ≥ 源码实际需要的 {need_s}",
              claimed >= need,
              f"源码用了 {need_s} 才有的语法 (`X | Y` 联合类型 / 内置泛型下标), "
              f"在 {claimed_s} 上会直接报错")
    print(f"  源码实际最低要求: Python {need_s}+")

    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
