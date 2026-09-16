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

    section("parse_video_info — 码率 / 采样率 / 阈值")

    def json_with(streams, format_=None):
        return {"streams": streams, "format": format_ or {}}

    full = "/tmp/test.mp4"
    base = "/tmp"

    # 码率: stream 级优先于 format 级
    data = json_with(
        [{"codec_type": "video", "codec_name": "h264",
          "width": 1920, "height": 1080, "r_frame_rate": "30/1",
          "bit_rate": "20000000"}],  # 20 Mbps stream
        {"bit_rate": "50000000"},  # 50 Mbps format（应被忽略）
    )
    info = parse_video_info(data, full, base, 30000, 48)
    check("stream 级码率优先于 format 级", info.bitrate_kbps == 20000.0,
          detail=f"got {info.bitrate_kbps}")

    # 码率: 两处都没有 → 0，不崩溃
    data = json_with(
        [{"codec_type": "video", "codec_name": "h264",
          "width": 1920, "height": 1080, "r_frame_rate": "30/1"}],
        {},
    )
    info = parse_video_info(data, full, base, 30000, 48)
    check("码率缺失 → 0 不崩溃", info.bitrate_kbps == 0.0)

    # 阈值: 正好等于阈值算达标？
    data = json_with(
        [{"codec_type": "video", "codec_name": "h264",
          "width": 1920, "height": 1080, "r_frame_rate": "30/1",
          "bit_rate": "30000000"},
         {"codec_type": "audio", "codec_name": "aac",
          "channels": 2, "sample_rate": "48000"}],
        {},
    )
    info = parse_video_info(data, full, base, 30000, 48)
    check("码率正好等于阈值算达标", info.is_passing is True)

    # 阈值: 低于阈值
    data = json_with(
        [{"codec_type": "video", "codec_name": "h264",
          "width": 1920, "height": 1080, "r_frame_rate": "30/1",
          "bit_rate": "29999000"},
         {"codec_type": "audio", "codec_name": "aac",
          "channels": 2, "sample_rate": "48000"}],
        {},
    )
    info = parse_video_info(data, full, base, 30000, 48)
    check("码率 29999 < 30000 → 不达标", info.is_passing is False)

    # 采样率: 无音频流
    data = json_with(
        [{"codec_type": "video", "codec_name": "h264",
          "width": 1920, "height": 1080, "r_frame_rate": "30/1",
          "bit_rate": "30000000"}],
        {},
    )
    info = parse_video_info(data, full, base, 30000, 48)
    check("无音频流: sample_rate_passing=True", info.sample_rate_passing is True)
    check("无音频流: is_passing=True（不受影响）", info.is_passing is True)

    # 采样率: 解析失败（非数字字符串）
    data = json_with(
        [{"codec_type": "video", "codec_name": "h264",
          "width": 1920, "height": 1080, "r_frame_rate": "30/1",
          "bit_rate": "30000000"},
         {"codec_type": "audio", "codec_name": "aac",
          "channels": 2, "sample_rate": "not_a_number"}],
        {},
    )
    info = parse_video_info(data, full, base, 30000, 48)
    check("采样率解析失败: sample_rate_passing=False", info.sample_rate_passing is False)
    check("采样率解析失败: is_passing=False", info.is_passing is False)

    # 采样率: 低于阈值
    data = json_with(
        [{"codec_type": "video", "codec_name": "h264",
          "width": 1920, "height": 1080, "r_frame_rate": "30/1",
          "bit_rate": "30000000"},
         {"codec_type": "audio", "codec_name": "aac",
          "channels": 2, "sample_rate": "44100"}],
        {},
    )
    info = parse_video_info(data, full, base, 30000, 48)
    check("采样率 44.1kHz < 48kHz → sample_rate_passing=False",
          info.sample_rate_passing is False)

    # 阈值字段透传
    info = parse_video_info(json_with(
        [{"codec_type": "video", "codec_name": "h264",
          "width": 1920, "height": 1080, "r_frame_rate": "30/1",
          "bit_rate": "30000000"}],
        {},
    ), full, base, 30000, 48)
    check("bitrate_std 透传", info.bitrate_std == 30000)
    check("sample_rate_std 透传", info.sample_rate_std == 48)

    section("parse_video_info — 标题 / 大小 / 路径")

    def base_video_streams():
        return [{"codec_type": "video", "codec_name": "h264",
                 "width": 1920, "height": 1080, "r_frame_rate": "30/1",
                 "bit_rate": "30000000"}]

    full = "/tmp/test.mp4"

    # 标题: tags 中大小写不敏感
    info = parse_video_info(
        {"streams": base_video_streams(),
         "format": {"tags": {"TITLE": "My Movie"}}},
        full, "/tmp", 30000, 48,
    )
    check("title 标签大小写不敏感", info.title == "My Movie")

    # 标题: tags 中无 title → 回退到无扩展文件名
    info = parse_video_info(
        {"streams": base_video_streams(),
         "format": {"tags": {"artist": "X"}}},
        full, "/tmp", 30000, 48,
    )
    check("title 缺失回退到文件名", info.title == "test")

    # 标题: 无 tags 字段
    info = parse_video_info(
        {"streams": base_video_streams(), "format": {}},
        full, "/tmp", 30000, 48,
    )
    check("无 tags 字段不崩溃", info.title == "test")

    # 文件大小: format.size 缺失
    with tempdir() as d:
        path = os.path.join(d, "x.mp4")
        open(path, "w").write("x" * 5000)
        info = parse_video_info(
            {"streams": base_video_streams(), "format": {}},
            path, d, 30000, 48,
        )
        check("size 缺失回退 os.path.getsize", "KB" in info.file_size or "B" in info.file_size,
              detail=f"got {info.file_size!r}")

    # 文件大小: size 非数字
    info = parse_video_info(
        {"streams": base_video_streams(),
         "format": {"size": "garbage"}},
        full, "/tmp", 30000, 48,
    )
    check("size 非数字 → N/A（不回退到真实大小）",
          info.file_size == "N/A",
          detail=f"got {info.file_size!r}")

    # 相对路径: 同目录 → ./
    info = parse_video_info(
        {"streams": base_video_streams(), "format": {}},
        "/tmp/a.mp4", "/tmp", 30000, 48,
    )
    check("rel_path 同目录 → ./", info.rel_path == "./",
          detail=f"got {info.rel_path!r}")

    # 相对路径: 子目录 → ./A/B
    info = parse_video_info(
        {"streams": base_video_streams(), "format": {}},
        "/tmp/A/B/c.mp4", "/tmp", 30000, 48,
    )
    check("rel_path 子目录 → ./A/B",
          info.rel_path == "./A/B",
          detail=f"got {info.rel_path!r}")

    # 相对路径: base 在另一个盘（Windows 跨盘 → ValueError）
    info = parse_video_info(
        {"streams": base_video_streams(), "format": {}},
        "C:\\foo\\x.mp4", "D:\\bar", 30000, 48,
    )
    check("rel_path 跨盘 → 回退绝对路径",
          info.rel_path == "C:\\foo\\x.mp4",
          detail=f"got {info.rel_path!r}")

    print(f"\n{'FAIL' if _failures else 'PASS'}: {len(_failures)} failure(s)")
    sys.exit(1 if _failures else 0)
