"""VideoGridView 列表头点击排序 一次性断言脚本。

验证：
- _extract_sort_key 对 4 个可排序列映射到正确的字段, 未知列抛 KeyError;
- _next_sort_state 3 态循环 + 跨列切换行为;
- sorted() 稳定排序保证同级保留插入序;
- _header_text_with_arrow 三态输出正确;
- 「结果」列 asc 把不达标排到前 (False < True).

运行: python docs/superpowers/specs/2026-10-13-sort-headers-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

from video_checker import (
    VideoInfo,
    _extract_sort_key,
    _next_sort_state,
    _header_text_with_arrow,
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


def make_info(
    rel: str = "a.mp4",
    is_passing: bool = True,
    bitrate_kbps: float = 1000.0,
    frame_rate_fps: float = 30.0,
    sample_rate_khz: float = 48.0,
) -> VideoInfo:
    return VideoInfo(
        rel_path=rel,
        title=rel,
        resolution="1920x1080",
        frame_rate=f"{frame_rate_fps:.2f} fps",
        bitrate_kbps=bitrate_kbps,
        video_codec="h264",
        audio_codec="aac",
        audio_channels="2",
        audio_sample_rate=f"{sample_rate_khz:.1f} kHz",
        duration="00:01:00",
        file_size="10 MB",
        is_passing=is_passing,
        sample_rate_passing=(sample_rate_khz >= 48),
        bitrate_std=30000.0,
        sample_rate_std=48.0,
        full_path=f"/tmp/{rel}",
        container_format="mp4",
        frame_rate_fps=frame_rate_fps,
        sample_rate_khz=sample_rate_khz,
    )


if __name__ == "__main__":
    section("_extract_sort_key — 可排序列字段映射")
    info = make_info(
        is_passing=False, bitrate_kbps=12345.0,
        frame_rate_fps=29.97, sample_rate_khz=44.1,
    )
    check("result → is_passing (False)",
          _extract_sort_key(info, 'result') is False)
    check("bitrate → bitrate_kbps (12345.0)",
          _extract_sort_key(info, 'bitrate') == 12345.0)
    check("frame_rate → frame_rate_fps (29.97)",
          _extract_sort_key(info, 'frame_rate') == 29.97)
    check("audio_sample_rate → sample_rate_khz (44.1)",
          _extract_sort_key(info, 'audio_sample_rate') == 44.1)

    raised = False
    try:
        _extract_sort_key(info, 'title')
    except KeyError:
        raised = True
    check("非排序列 'title' 抛 KeyError", raised)

    section("_next_sort_state — 同一列 3 态循环")
    # idle → click result
    k, d = _next_sort_state(None, False, 'result')
    check("idle + click result → ('result', asc)",
          k == 'result' and d is False)
    # asc → desc
    k, d = _next_sort_state('result', False, 'result')
    check("asc + click result → ('result', desc)",
          k == 'result' and d is True)
    # desc → clear
    k, d = _next_sort_state('result', True, 'result')
    check("desc + click result → (None, False)",
          k is None and d is False)
    # 再点恢复升序
    k, d = _next_sort_state(None, False, 'result')
    check("cleared + click result → ('result', asc)",
          k == 'result' and d is False)

    section("_next_sort_state — 跨列切换到升序")
    # 当前是 bitrate desc, 点 result → (result, asc)
    k, d = _next_sort_state('bitrate', True, 'result')
    check("bitrate desc + click result → ('result', asc)",
          k == 'result' and d is False)
    # 当前是 result asc, 点 bitrate → (bitrate, asc)
    k, d = _next_sort_state('result', False, 'bitrate')
    check("result asc + click bitrate → ('bitrate', asc)",
          k == 'bitrate' and d is False)

    section("稳定排序 — 同级保留插入序")
    infos = [
        make_info(rel="a.mp4", is_passing=False, bitrate_kbps=5000.0),
        make_info(rel="b.mp4", is_passing=True, bitrate_kbps=5000.0),
        make_info(rel="c.mp4", is_passing=False, bitrate_kbps=5000.0),
        make_info(rel="d.mp4", is_passing=True, bitrate_kbps=5000.0),
        make_info(rel="e.mp4", is_passing=False, bitrate_kbps=5000.0),
    ]
    sorted_asc = sorted(
        infos, key=lambda i: _extract_sort_key(i, 'bitrate'),
    )
    check("asc by bitrate (全部同级) → 插入序保留",
          [i.rel_path for i in sorted_asc]
          == ["a.mp4", "b.mp4", "c.mp4", "d.mp4", "e.mp4"])
    sorted_desc = sorted(
        infos, key=lambda i: _extract_sort_key(i, 'bitrate'), reverse=True,
    )
    check("desc by bitrate (全部同级) → 插入序保留",
          [i.rel_path for i in sorted_desc]
          == ["a.mp4", "b.mp4", "c.mp4", "d.mp4", "e.mp4"])

    section("排序 — asc by result = 不达标优先")
    infos2 = [
        make_info(rel="pass1.mp4", is_passing=True),
        make_info(rel="fail1.mp4", is_passing=False),
        make_info(rel="pass2.mp4", is_passing=True),
        make_info(rel="fail2.mp4", is_passing=False),
    ]
    sorted_asc = sorted(
        infos2, key=lambda i: _extract_sort_key(i, 'result'),
    )
    check("asc by result → [fail, fail, pass, pass]",
          [i.rel_path for i in sorted_asc]
          == ["fail1.mp4", "fail2.mp4", "pass1.mp4", "pass2.mp4"])

    section("排序 — asc/desc by bitrate 数值正确")
    infos3 = [
        make_info(rel="low.mp4", bitrate_kbps=1000.0),
        make_info(rel="mid.mp4", bitrate_kbps=5000.0),
        make_info(rel="high.mp4", bitrate_kbps=9000.0),
    ]
    sorted_asc = sorted(
        infos3, key=lambda i: _extract_sort_key(i, 'bitrate'),
    )
    check("asc by bitrate → [low, mid, high]",
          [i.rel_path for i in sorted_asc]
          == ["low.mp4", "mid.mp4", "high.mp4"])
    sorted_desc = sorted(
        infos3, key=lambda i: _extract_sort_key(i, 'bitrate'), reverse=True,
    )
    check("desc by bitrate → [high, mid, low]",
          [i.rel_path for i in sorted_desc]
          == ["high.mp4", "mid.mp4", "low.mp4"])

    section("_header_text_with_arrow — 表头文本")
    check("未激活列 → 无箭头",
          _header_text_with_arrow('结果', False, False) == '结果')
    check("激活升序 → '结果 ▲'",
          _header_text_with_arrow('结果', True, False) == '结果 ▲')
    check("激活降序 → '结果 ▼'",
          _header_text_with_arrow('结果', True, True) == '结果 ▼')

    print(f"\n{'FAIL' if _failures else 'PASS'}: {len(_failures)} failure(s)")
    sys.exit(1 if _failures else 0)
