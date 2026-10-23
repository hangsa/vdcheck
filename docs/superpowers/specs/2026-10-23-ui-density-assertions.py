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

    section("SnapshotProgressDialog — 横向滚动条")
    import tempfile
    from video_checker import SnapshotProgressDialog

    dlg = SnapshotProgressDialog(root, [make_info(title='a.mp4', rel_path='./')],
                                tempfile.mkdtemp())
    root.update_idletasks()
    check("弹窗有 _hsb", hasattr(dlg, '_hsb'))
    hsb = getattr(dlg, '_hsb', None)
    check("_hsb 是 Scrollbar", hsb is not None and hsb.winfo_class() == 'TScrollbar',
          f"got={hsb.winfo_class() if hsb else None!r}")
    check("_hsb.master 是 _middle (不能是 top)",
          hsb is not None and hsb.master is dlg._middle,
          f"master={hsb.master if hsb else None!r}")
    check("_hsb 已 pack 进布局", hsb is not None and hsb.winfo_manager() == 'pack',
          f"manager={hsb.winfo_manager() if hsb else None!r}")
    # 注意: 不能用 'xscroll' in cget('xscrollcommand') 判定 —— tkinter 把 Python
    # 回调注册成 Tcl 命令再存回选项, cget 拿到的是 '<id>set' 这种字符串化的
    # 回调 id (跟 cget('command') 同一个坑), 里面并没有 'xscroll' 字样。
    # 判定改成「选项非空」+ 下面 hsb 位置随 canvas 滚动的功能断言。
    check("canvas 绑了 xscrollcommand", bool(dlg.canvas.cget('xscrollcommand')),
          f"got={dlg.canvas.cget('xscrollcommand')!r}")

    # 功能验证: 5 个时间点 → 内容 45+250+5*328=1935 > 窗口 1200, 必须能横滚
    # 注意不要断言 hsb.cget('command') == canvas.xview —— tkinter 返回的是
    # 字符串化的回调 id ('4385533376xview'), 不是 bound method, 那样断言必挂。
    dlg._update_table(['00:00:01:00'] * 5)
    root.update_idletasks()
    first, last = dlg.canvas.xview()
    check("5 个时间点时内容宽出视口 (xview 可见比 < 1)", last - first < 1.0,
          f"xview={dlg.canvas.xview()}")
    dlg.canvas.xview_moveto(1.0)
    root.update_idletasks()
    check("横向滚动到底 (xview 右端 = 1.0)",
          abs(dlg.canvas.xview()[1] - 1.0) < 1e-6, f"xview={dlg.canvas.xview()}")
    check("横向滚动生效 (xview 左端 > 0)", dlg.canvas.xview()[0] > 0.0,
          f"xview={dlg.canvas.xview()}")
    check("xscrollcommand 驱动 hsb 位置",
          hsb is not None and hsb.get()[1] > 0.99, f"hsb={hsb.get() if hsb else None}")

    section("SnapshotProgressDialog — 表头/数据列对齐 (横向滚动不脱节)")
    # 表头与数据行在 _inner 的同一个 grid 里 (表头 row 0, 数据 row 1..N), 共用同一个
    # scrollregion, 横滚时一起动。对应关系: 表头第 k 个 label ↔ 第 0 行第 k 个 cell。
    # winfo_rootx 是窗口级坐标, 不受 canvas 视口裁剪影响; 两者同属一个 window item,
    # 滚动时同增同减, 所以两者的差值就是列对齐误差。误差要求 0px: 同 grid 分配是
    # 结构性对齐, 给了容差就测不出真正的回归。
    # 表头必须用 _header_labels 显式取, 不能靠「_inner 里 row==0 的 widget」反推 ——
    # 表头被移出 canvas (钉在视口不动) 时, row 0 上坐的是第一行数据, 那样比等于
    # 拿数据跟数据比, 恒等于 0, 断言会假绿。
    hdrs = getattr(dlg, '_header_labels', None)
    check("弹窗暴露表头 label 列表", hdrs is not None,
          f"got={[w.cget('text') for w in hdrs] if hdrs else hdrs}")
    hdrs = hdrs or []
    body0 = [dlg._row_idx_labels[0], dlg._row_title_labels[0], *dlg._cells[0]]
    check("表头列数 == 数据列数 (序号+标题+5 个时间点)",
          len(hdrs) == len(body0) == 7, f"hdr={len(hdrs)} body={len(body0)}")
    check("表头在 _inner 的 row 0 (与数据行同一 scrollregion)",
          all(w.master is dlg._inner and int(w.grid_info()['row']) == 0
              for w in hdrs),
          f"rows={[int(w.grid_info()['row']) for w in hdrs]}")
    check("表头第 k 列与第 0 行第 k 个 cell 同列",
          [int(w.grid_info()['column']) for w in hdrs] == [0, 1, 2, 3, 4, 5, 6])
    # 两个滚动位置各测一次: 验的是「始终一起对齐」, 而不是某一帧碰巧对得上。
    for tag, moveto in (("滚动位置 0", 0.0), ("滚到最右", 1.0)):
        dlg.canvas.xview_moveto(moveto)
        root.update_idletasks()
        detail = []
        worst = -1   # 起点 -1: 表头列表为空时「无对比」不得算通过
        for h, b in zip(hdrs, body0):
            hx, bx = h.winfo_rootx(), b.winfo_rootx()
            worst = max(worst, abs(hx - bx))
            detail.append(f"{h.cget('text')}={hx}/{bx}")
        check(f"{tag}: 表头 x 与数据 cell x 对齐 (误差 0px)", worst == 0,
              f"Δmax={worst}  " + " ".join(detail))

    section("SnapshotProgressDialog — 滚轮绑定 (事件不向父 widget 冒泡)")
    # Tk 事件只沿 bindtags (自身/class/toplevel/all) 派发, 不向父 widget 冒泡, 所以
    # 绑在 canvas/_inner 上并不能覆盖停在子 widget 上的滚轮 —— 每个要能滚的 widget
    # 都得各自显式绑。cell 占了弹窗绝大部分面积, 漏绑等于滚轮在内容区基本是死的。
    def wheel_targets(d):
        """所有应该接滚轮的 widget: canvas / _inner / 表头 / 第 0 行数据。"""
        return [('canvas', d.canvas), ('_inner', d._inner),
                *[(f'表头 {w.cget("text")}', w) for w in d._header_labels],
                *[(f'第0行 cell[{c}]', w) for c, w in enumerate(d._cells[0])],
                ('第0行 序号', d._row_idx_labels[0]),
                ('第0行 标题', d._row_title_labels[0])]

    targets = wheel_targets(dlg)
    check("受检滚轮目标数 (canvas+_inner+7 表头+5 cell+序号+标题=16)",
          len(targets) == 16, f"got={len(targets)}")
    missing = [tag for tag, w in targets if not w.bind('<MouseWheel>')]
    check("canvas/_inner/表头/数据 cell 都挂了 <MouseWheel>",
          not missing, f"未绑={missing}")
    missing45 = [tag for tag, w in targets
                 if not w.bind('<Button-4>') or not w.bind('<Button-5>')]
    check("canvas/_inner/表头/数据 cell 都挂了 <Button-4>/<Button-5> (X11 滚轮)",
          not missing45, f"未绑={missing45}")

    section("SnapshotProgressDialog — 空文件列表不崩")
    dlg2 = SnapshotProgressDialog(root, [], tempfile.mkdtemp())
    root.update_idletasks()
    # 空 files: _inner 里只剩 row 0 的表头 label, 一个数据行 (row >= 1) 都不该有。
    rows = [w for w in dlg2._inner.winfo_children()
            if int(w.grid_info().get('row', 0)) != 0]
    check("空 files 不建任何数据行", rows == [],
          f"数据行={len(rows)} _inner 总 children={len(dlg2._inner.winfo_children())}")
    dlg2.top.destroy(); dlg.top.destroy()

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