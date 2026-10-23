"""界面信息展示效率优化 一次性断言脚本。

验证：
- format_title 全部 rel_path 形态 (含跨盘符绝对路径 / UNC);
- truncate_text 像素级截断 (ASCII / CJK / 边界);
- VideoGridView._truncate_text 委托到模块级实现且行为不变。

运行: python docs/superpowers/specs/2026-10-23-ui-density-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from video_checker import VideoInfo, VideoGridView, format_title, truncate_text


_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "✓" if condition else "✗"
    line = f"  {mark} {name}" + (f" — {detail}" if detail else "")
    print(line)
    if not condition:
        _failures.append(line)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def make_info(title='a.mp4', rel_path='./', **kw) -> VideoInfo:
    """构造测试用 VideoInfo (只填 format_title 用得到的字段)。"""
    base = dict(
        rel_path=rel_path, title=title, resolution='1920x1080',
        frame_rate='25.00', bitrate_kbps=32000.0, video_codec='h264',
        audio_codec='aac', audio_channels='2', audio_sample_rate='48.0',
        duration='00:01:00', file_size='1.0 GB', is_passing=True,
        sample_rate_passing=True, bitrate_std=30000.0, sample_rate_std=48.0,
        full_path='/x/a.mp4', container_format='mp4',
    )
    base.update(kw)
    return VideoInfo(**base)


def main() -> None:
    section("format_title — rel_path 各种形态")
    cases = [
        ('./',        'a.mp4', './a.mp4'),
        ('./A',       'a.mp4', './A/a.mp4'),
        ('./A/B',     'a.mp4', './A/B/a.mp4'),
        ('./A/B/',    'a.mp4', './A/B/a.mp4'),
        ('',          'a.mp4', './a.mp4'),
        ('D:\\videos\\a.mp4',  'a.mp4', 'D:\\videos\\a.mp4'),
        ('\\\\srv\\share\\a.mp4', 'a.mp4', '\\\\srv\\share\\a.mp4'),
    ]
    for rel, title, want in cases:
        got = format_title(make_info(title=title, rel_path=rel))
        check(f"rel_path={rel!r} → {want!r}", got == want, f"got={got!r}")

    section("format_title — 绝对路径不重复文件名 (跨盘符既有 bug)")
    got = format_title(make_info(rel_path='D:\\videos\\a.mp4'))
    check("绝对路径只出现一次", got.count('a.mp4') == 1, f"got={got!r}")

    root = tk.Tk()
    root.withdraw()
    font = tkfont.Font(root=root, font=('TkDefaultFont', 9))

    section("truncate_text — 像素级截断")
    short = 'ab.mp4'
    check("短文本原样返回", truncate_text(short, 250, font) == short,
          f"got={truncate_text(short, 250, font)!r}")

    long_ascii = 'a' * 200
    got = truncate_text(long_ascii, 100, font)
    check("超长 ASCII 被截断", len(got) < len(long_ascii), f"len={len(got)}")
    check("截断结果以 ... 结尾", got.endswith('...'), f"got={got[-10:]!r}")
    check("截断结果不超列宽", font.measure(got) <= 100 - 10,
          f"w={font.measure(got)}")

    long_cjk = '中文标题' * 40
    got = truncate_text(long_cjk, 100, font)
    check("超长 CJK 被截断", font.measure(got) <= 100 - 10,
          f"w={font.measure(got)}")

    got = truncate_text('anything', 5, font)
    check("列宽 < 10 → 返回 '...'", got == '...', f"got={got!r}")

    got = truncate_text('', 250, font)
    check("空串原样返回", got == '', f"got={got!r}")

    section("VideoGridView._truncate_text — 委托且行为一致")
    grid = VideoGridView(ttk.Frame(root), {'title': ('标题', 250)})
    check("方法委托给模块级 truncate_text",
          grid._truncate_text('a' * 200, 100)
          == truncate_text('a' * 200, 100, grid._cell_font))
    check("短文本不受影响", grid._truncate_text('a.mp4', 250) == 'a.mp4')

    root.destroy()

    print(f"\n{'FAIL' if _failures else 'PASS'}: {len(_failures)} failure(s)")
    sys.exit(1 if _failures else 0)


if __name__ == "__main__":
    main()