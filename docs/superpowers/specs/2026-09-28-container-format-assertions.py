"""Container format 字段一次性断言脚本。

运行: python docs/superpowers/specs/2026-09-28-container-format-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

from video_checker import parse_video_info, VideoInfo


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
    section("VideoInfo.container_format 字段存在")

    info = VideoInfo(
        rel_path="./", title="t", resolution="1920x1080",
        frame_rate="30.00 fps", bitrate_kbps=30000.0,
        video_codec="h264", audio_codec="aac",
        audio_channels="2", audio_sample_rate="48.0 kHz",
        duration="00:01:00", file_size="1 MB",
        is_passing=True, sample_rate_passing=True,
        bitrate_std=30000, sample_rate_std=48,
        full_path="/tmp/t.mp4",
        container_format="mp4",
    )
    check("VideoInfo 接受 container_format", getattr(info, "container_format", None) == "mp4",
          detail=f"got {getattr(info, 'container_format', 'MISSING')!r}")

    section("parse_video_info — container_format 正常情况")

    def base_streams():
        return [{"codec_type": "video", "codec_name": "h264",
                 "width": 1920, "height": 1080, "r_frame_rate": "30/1",
                 "bit_rate": "30000000"}]

    full = "/tmp/test.mp4"
    base = "/tmp"

    # MP4: format_name 是逗号列表，取第一项
    data = {"streams": base_streams(),
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"}}
    info = parse_video_info(data, full, base, 30000, 48)
    check("mp4 多 format_name → 'mp4'", info.container_format == "mp4",
          detail=f"got {info.container_format!r}")

    # MKV: format_name 通常是 matroska
    full_mkv = "/tmp/test.mkv"
    data = {"streams": base_streams(),
            "format": {"format_name": "matroska,webm"}}
    info = parse_video_info(data, full_mkv, base, 30000, 48)
    check("mkv → 'matroska'", info.container_format == "matroska",
          detail=f"got {info.container_format!r}")

    section("parse_video_info — 兜底边界")

    # format_name 是空字符串 → 回退到扩展名
    data = {"streams": base_streams(), "format": {"format_name": ""}}
    info = parse_video_info(data, full, base, 30000, 48)
    check("format_name 空 → 回退扩展名 'mp4'", info.container_format == "mp4",
          detail=f"got {info.container_format!r}")

    # format 完全没有 format_name 字段 → 回退到扩展名
    data = {"streams": base_streams(), "format": {}}
    info = parse_video_info(data, full, base, 30000, 48)
    check("无 format_name 字段 → 回退扩展名 'mp4'", info.container_format == "mp4",
          detail=f"got {info.container_format!r}")

    # 大写扩展名 → 小写
    full_upper = "/tmp/test.MKV"
    data = {"streams": base_streams(), "format": {}}
    info = parse_video_info(data, full_upper, base, 30000, 48)
    check("大写扩展名 → 小写 'mkv'", info.container_format == "mkv",
          detail=f"got {info.container_format!r}")

    # 无扩展名 → 'N/A'
    full_noext = "/tmp/test_no_extension"
    data = {"streams": base_streams(), "format": {}}
    info = parse_video_info(data, full_noext, base, 30000, 48)
    check("无扩展名 → 'N/A'", info.container_format == "N/A",
          detail=f"got {info.container_format!r}")

    # format_name 只有一项，没有逗号
    full_ts = "/tmp/test.ts"
    data = {"streams": base_streams(), "format": {"format_name": "mpegts"}}
    info = parse_video_info(data, full_ts, base, 30000, 48)
    check("单一项 format_name → 'mpegts'",
          info.container_format == "mpegts",
          detail=f"got {info.container_format!r}")

    print(f"\n{'FAIL' if _failures else 'PASS'}: {len(_failures)} failure(s)")
    sys.exit(1 if _failures else 0)
