"""定点截图 纯函数 一次性断言脚本。

验证：
- get_ffmpeg_path 返回非空字符串
- parse_time_token 合法 / 非法输入
- time_token_to_string 替换 ':' 为 '-'

运行: python docs/superpowers/specs/2026-10-16-snapshot-assertions.py
退出码 0 = 全部通过; 非 0 = 有失败。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

from video_checker import (
    get_ffmpeg_path,
    parse_time_token,
    time_token_to_string,
)


_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "✓" if condition else "✗"
    line = f"  {mark} {name}" + (f" — {detail}" if detail else "")
    print(line)
    if not condition:
        _failures.append(line)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# === get_ffmpeg_path ===

if __name__ == "__main__":
    section("get_ffmpeg_path — 返回非空字符串")
    p = get_ffmpeg_path()
    check("get_ffmpeg_path 返回非空", bool(p), f"got={p!r}")
    check("get_ffmpeg_path 返回字符串", isinstance(p, str))

    section("parse_time_token — 合法输入")
    check("00:00:10:00 @ 30fps = 10.0",
          parse_time_token("00:00:10:00", 30.0) == 10.0,
          f"got={parse_time_token('00:00:10:00', 30.0)!r}")
    check("01:30:45:15 @ 30fps = 5445.5",
          parse_time_token("01:30:45:15", 30.0) == 5445 + 15/30,
          f"got={parse_time_token('01:30:45:15', 30.0)!r}")
    check("00:00:00:1 @ 60fps ≈ 0.01667",
          abs(parse_time_token("00:00:00:1", 60.0) - 1/60) < 1e-9,
          f"got={parse_time_token('00:00:00:1', 60.0)!r}")
    check("frame=0 @ 30fps = 整数秒",
          parse_time_token("00:01:23:0", 30.0) == 83.0)

    section("parse_time_token — 非法输入 ValueError")
    for bad in ["1:2:3", "1:2:3:4:5", "aa:bb:cc:dd", "00:60:00:00",
                "00:00:60:00", "-1:00:00:00", "00:00:00:-1"]:
        raised = False
        try:
            parse_time_token(bad, 30.0)
        except ValueError:
            raised = True
        check(f"非法 {bad!r} 抛 ValueError", raised)

    # fps 越界
    raised = False
    try:
        parse_time_token("00:00:00:00", 0)
    except ValueError:
        raised = True
    check("fps=0 抛 ValueError", raised)

    raised = False
    try:
        parse_time_token("00:00:00:00", -1.0)
    except ValueError:
        raised = True
    check("fps<0 抛 ValueError", raised)

    section("time_token_to_string — ':' → '-'")
    check("00:00:10:00 → 00-00-10-00",
          time_token_to_string("00:00:10:00") == "00-00-10-00")
    check("01:30:45:15 → 01-30-45-15",
          time_token_to_string("01:30:45:15") == "01-30-45-15")
    check("无冒号 → 原样",
          time_token_to_string("no-colon-here") == "no-colon-here")

    print(f"\n{'FAIL' if _failures else 'PASS'}: {len(_failures)} failure(s)")
    sys.exit(1 if _failures else 0)
