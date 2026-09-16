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
    print("Bug 审计断言")

    section("scan_video_files")

    with tempdir() as d:
        # 创建混合内容: 视频、嵌套视频、非视频文件
        os.makedirs(os.path.join(d, "sub"))
        for name in ["a.mp4", "b.MKV", "c.avi", "d.txt"]:
            open(os.path.join(d, name), "w").close()
        for name in ["e.mp4", "f.mov"]:
            open(os.path.join(d, "sub", name), "w").close()

        # 非递归: 只顶层
        result = sorted(scan_video_files(d, recursive=False))
        expected = sorted([os.path.join(d, n) for n in ["a.mp4", "b.MKV", "c.avi"]])
        check("non-recursive 仅顶层视频", result == expected,
              detail=f"got {result}")

        # 递归: 包含 sub/
        result = sorted(scan_video_files(d, recursive=True))
        expected = sorted([
            os.path.join(d, "a.mp4"), os.path.join(d, "b.MKV"), os.path.join(d, "c.avi"),
            os.path.join(d, "sub", "e.mp4"), os.path.join(d, "sub", "f.mov"),
        ])
        check("recursive 包含子目录", result == expected, detail=f"got {result}")

        # 大小写: .MKV 是否被识别为视频
        check("大写扩展名识别", os.path.join(d, "b.MKV") in scan_video_files(d, False))

        # 非视频文件不被包含
        check("非视频文件排除", not any("d.txt" in p for p in scan_video_files(d, True)))

        # 空目录: 不抛错
        empty_d = os.path.join(d, "nonexistent_or_empty")
        result = scan_video_files(empty_d, recursive=False)
        check("不存在目录不抛错", result == [], detail=f"got {result}")

    print(f"\n{'FAIL' if _failures else 'PASS'}: {len(_failures)} failure(s)")
    sys.exit(1 if _failures else 0)
