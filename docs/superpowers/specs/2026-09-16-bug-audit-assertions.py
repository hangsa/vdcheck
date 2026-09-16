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

    section("parse_video_info — 基本字段与边界")

    def make_ffprobe_json(**overrides):
        """基础 ffprobe JSON，包含一个视频流 + 一个音频流。"""
        base = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264",
                 "width": 1920, "height": 1080, "r_frame_rate": "30/1",
                 "bit_rate": "30000000"},
                {"codec_type": "audio", "codec_name": "aac",
                 "channels": 2, "sample_rate": "48000"},
            ],
            "format": {"duration": "120.0", "size": "100000000", "bit_rate": "31000000"},
        }
        # 深度合并覆盖（streams 列表整体替换）
        if "streams" in overrides:
            base["streams"] = overrides.pop("streams")
        if "format" in overrides:
            base["format"].update(overrides.pop("format"))
        base.update(overrides)
        return base

    full = "/tmp/test_video.mp4"
    base = "/tmp"

    # 1. 正常情况
    info = parse_video_info(make_ffprobe_json(), full, base, 30000, 48)
    check("正常: is_passing=True", info.is_passing is True)
    check("正常: bitrate=30000", info.bitrate_kbps == 30000.0)
    check("正常: resolution=1920x1080", info.resolution == "1920x1080")
    check("正常: frame_rate", info.frame_rate == "30.00 fps")
    check("正常: sample_rate_passing", info.sample_rate_passing is True)

    # 2. 无视频流 → 返回 None
    info = parse_video_info({"streams": [
        {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000"},
    ], "format": {}}, full, base, 30000, 48)
    check("无视频流返回 None", info is None)

    # 3. r_frame_rate = 0/0
    info = parse_video_info(make_ffprobe_json(streams=[
        {"codec_type": "video", "codec_name": "h264",
         "width": 1920, "height": 1080, "r_frame_rate": "0/0",
         "bit_rate": "30000000"},
        {"codec_type": "audio", "codec_name": "aac",
         "channels": 2, "sample_rate": "48000"},
    ]), full, base, 30000, 48)
    check("r_frame_rate=0/0 不崩溃", info is not None)
    check("r_frame_rate=0/0 显示 N/A", info.frame_rate == "N/A")

    # 4. r_frame_rate 非法字符串
    info = parse_video_info(make_ffprobe_json(streams=[
        {"codec_type": "video", "codec_name": "h264",
         "width": 1920, "height": 1080, "r_frame_rate": "abc",
         "bit_rate": "30000000"},
    ]), full, base, 30000, 48)
    check("r_frame_rate 非法 → N/A", info.frame_rate == "N/A")

    print(f"\n{'FAIL' if _failures else 'PASS'}: {len(_failures)} failure(s)")
    sys.exit(1 if _failures else 0)
