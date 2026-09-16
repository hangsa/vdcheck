"""Bug 审计一次性断言脚本。

不进入 CI；用于交叉验证静态走查推断。
运行: python docs/superpowers/specs/2026-09-16-bug-audit-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import sys
import json
import tempfile
import shutil
from contextlib import contextmanager

# 让脚本能找到仓库根下的 video_checker.py
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

from video_checker import (
    scan_video_files,
    parse_video_info,
    _cell_fg,
    VideoInfo,
    VideoGridView,
    format_duration,
    format_file_size,
    VideoCheckerApp,
)


@contextmanager
def tempdir():
    d = tempfile.mkdtemp()
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "✓" if condition else "✗"
    line = f"  {mark} {name}" + (f" — {detail}" if detail else "")
    print(line)
    if not condition:
        _failures.append(line)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


if __name__ == "__main__":
    # 后续 task 用 Edit 追加 section(...) 与 check(...)
    print("Bug 审计断言")
    print(f"\n{'FAIL' if _failures else 'PASS'}: {len(_failures)} failure(s)")
    sys.exit(1 if _failures else 0)
