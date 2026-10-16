"""定点截图 纯函数 一次性断言脚本。

验证：
- get_ffmpeg_path 返回非空字符串
- parse_time_token 合法 / 非法输入
- time_token_to_string 替换 ':' 为 '-'

运行: python docs/superpowers/specs/2026-10-16-snapshot-assertions.py
退出码 0 = 全部通过; 非 0 = 有失败。
"""
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

from video_checker import (
    get_ffmpeg_path,
    parse_time_token,
    time_token_to_string,
    extract_frame,
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

    section("extract_frame — ffmpeg 抽帧 (需 ffmpeg 可用)")
    if not (shutil.which('ffmpeg') or os.path.exists(get_ffmpeg_path())):
        print("  ⊘ 跳过: 系统中无 ffmpeg")
    else:
        with tempfile.TemporaryDirectory() as td:
            # 合成 1 秒 30fps 测试视频
            test_mp4 = os.path.join(td, "test.mp4")
            subprocess.run([
                shutil.which('ffmpeg') or get_ffmpeg_path(),
                '-y', '-f', 'lavfi', '-i',
                'color=c=red:s=320x240:d=1:r=30',
                '-pix_fmt', 'yuv420p',
                test_mp4,
            ], check=True, capture_output=True)
            check("合成测试视频成功", os.path.exists(test_mp4))

            out_png = os.path.join(td, "frame.png")
            ok = extract_frame(test_mp4, 0.5, out_png)
            check("extract_frame 0.5s 成功", ok)
            check("输出 png 存在", os.path.exists(out_png))
            check("输出 png 非空", os.path.getsize(out_png) > 0)

            # 验证 png magic bytes (89 50 4E 47 0D 0A 1A 0A)
            with open(out_png, 'rb') as f:
                head = f.read(8)
            check("输出是 PNG (89 50 4E 47 0D 0A 1A 0A)",
                  head == b'\x89PNG\r\n\x1a\n',
                  f"got={head!r}")

        # 错误情况: 不存在的文件
        ok = extract_frame("/nonexistent/video.mp4", 0.0, "/tmp/out.png")
        check("不存在文件 → False", ok is False)

        # 错误情况: 损坏文件
        with tempfile.TemporaryDirectory() as td:
            bad = os.path.join(td, "bad.mp4")
            with open(bad, 'wb') as f:
                f.write(b'this is not a video')
            out = os.path.join(td, "out.png")
            ok = extract_frame(bad, 0.0, out)
            check("损坏文件 → False", ok is False)
            check("损坏文件 → 无输出", not os.path.exists(out))

    print(f"\n{'FAIL' if _failures else 'PASS'}: {len(_failures)} failure(s)")
    sys.exit(1 if _failures else 0)
