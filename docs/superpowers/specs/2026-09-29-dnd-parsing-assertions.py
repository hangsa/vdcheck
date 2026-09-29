"""Drag-drop 解析一次性断言脚本。

验证 _parse_drop_data 对 Windows / macOS / Linux DND data 格式的解析。

运行: /Users/longsa/.local/bin/python3.11 docs/superpowers/specs/2026-09-29-dnd-parsing-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

from video_checker import _parse_drop_data


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
    section("空 / 边界")
    check("空字符串 → []", _parse_drop_data("") == [])
    check("全空白 → []", _parse_drop_data("   \n\t  ") == [])
    check("前后空格被 strip", _parse_drop_data("  /a.mp4  ") == ["/a.mp4"])

    section("Windows 大括号格式")
    check("单文件 {file.mp4}", _parse_drop_data("{file.mp4}") == ["file.mp4"])
    check("多文件 {a.mp4} {b.mkv}",
          _parse_drop_data("{a.mp4} {b.mkv}") == ["a.mp4", "b.mkv"])
    check("带路径 {C:\\path\\a.mp4}",
          _parse_drop_data("{C:\\path\\a.mp4}") == ["C:\\path\\a.mp4"])
    check("带空格的路径 {C:\\path with space\\a.mp4}",
          _parse_drop_data("{C:\\path with space\\a.mp4}") == ["C:\\path with space\\a.mp4"])
    check("多个含空格路径",
          _parse_drop_data("{C:\\a b.mp4} {D:\\c d.mkv}") == ["C:\\a b.mp4", "D:\\c d.mkv"])

    section("macOS / Linux 空格分隔格式")
    check("Unix 单文件 /path/file.mp4",
          _parse_drop_data("/path/file.mp4") == ["/path/file.mp4"])
    check("Unix 多文件 (无空格路径)",
          _parse_drop_data("file1.mp4 file2.mkv") == ["file1.mp4", "file2.mkv"])
    check("Unix 多文件 (绝对路径, 无空格)",
          _parse_drop_data("/Users/foo/a.mp4 /Users/foo/b.mkv") ==
          ["/Users/foo/a.mp4", "/Users/foo/b.mkv"])
    check("Unix 多个不同扩展名",
          _parse_drop_data("/a.mp4 /b.mkv /c.avi /d.mov") ==
          ["/a.mp4", "/b.mkv", "/c.avi", "/d.mov"])

    section("混合 / 其它")
    check("Windows 单文件 (含 strip)",
          _parse_drop_data("  {file.mp4}  ") == ["file.mp4"])
    check("空大括号 → [] (回退到 split)",
          _parse_drop_data("{}") == ["{}"] or
          _parse_drop_data("{}") == [])

    print(f"\n{'FAIL' if _failures else 'PASS'}: {len(_failures)} failure(s)")
    sys.exit(1 if _failures else 0)