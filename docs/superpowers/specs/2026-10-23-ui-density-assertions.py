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


def app_widgets_body() -> str:
    """读 video_checker.py 的 _create_widgets 函数体 (headers 定义在那里)。"""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(_HERE))), 'video_checker.py')
    # newline='' 必须: 源码是 CRLF, 不指定的话 Python 会把 \r\n 转成 \n,
    # 下标/正则对不上源文件。这里只读不写。
    src = open(path, encoding='utf-8', newline='').read()
    return src[src.index("    def _create_widgets(self):"):
               src.index("    # ---- 事件处理 ----")]


def source_text() -> str:
    """读整个 video_checker.py 源码 (原样, 不做行尾转换)。"""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(_HERE))), 'video_checker.py')
    # newline='' 必须, 理由同 app_widgets_body: 源码是 CRLF
    return open(path, encoding='utf-8', newline='').read()


def method_body(method: str) -> str:
    """截出某个方法的源码 (从 'def <method>(self' 到下一个同缩进的 def)。"""
    src = source_text()
    start = src.index(f"    def {method}(self")
    rest = src[start + 1:]
    end = rest.index("\n    def ")
    return src[start:start + 1 + end]


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

    section("_format_cell — title 分支委托 format_title")
    for rel, title, want in cases:
        info = make_info(title=title, rel_path=rel)
        got = VideoGridView._format_cell(info, 'title')
        check(f"_format_cell rel_path={rel!r} → {want!r}", got == want,
              f"got={got!r}")

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
    # scrollregion。对应关系: 表头第 k 个 label ↔ 第 0 行第 k 个 cell。
    # winfo_rootx 是窗口级坐标, 不受 canvas 视口裁剪影响, 两者差值即列对齐误差。
    # 误差要求 0px: 同 grid 分配是结构性对齐, 给了容差就测不出真正的回归。
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
    # 纵向取舍 —— 已知且被接受的行为, 写在这里是为了防止以后被当成 bug 顺手"修掉"。
    # 表头是 _inner 的孩子 ⇒ 纵向滚动时随内容一起滚出视口, 不钉在顶部。
    # 不钉住是刻意的: 钉住需要「表头改回 canvas 的兄弟 + 按 canvasx 手工 place
    # 同步 x 偏移」这套机制, 本机 macOS 上无法验证而部署目标是 Windows, 风险高于收益。
    # 横向/纵向不可同日而语 —— 横向错位是「错误信息」(表头可见却指着错的列), 纵向滚走
    # 只是「缺失信息」(滚回去就有, 不产生错误结论)。横向对齐是正确性问题, 必须做到
    # Δ=0; 纵向不是。
    check("纵向取舍: 表头不钉住 (master 是 _inner ⇒ 随内容滚出视口)",
          bool(hdrs) and all(w.master is dlg._inner for w in hdrs),
          f"master 集合={sorted({w.master.__class__.__name__ for w in hdrs}) if hdrs else '无表头'}")
    check("表头第 k 列与第 0 行第 k 个 cell 同列",
          [int(w.grid_info()['column']) for w in hdrs] == [0, 1, 2, 3, 4, 5, 6])
    # 两个滚动位置各跑一次, 但**它验不到滚动耦合**: 本脚本 root.withdraw(), 弹窗
    # 从未 map, canvas 不会因 xview 变化去重排 window item 里的 widget —— 实测两个
    # 位置读到的 winfo_rootx 一字不差, 两次喂进去的是同一份输入。所以这里验的只是
    # 「两者在同一个 grid 里的相对对齐恒为 0」。滚动耦合由上面那条 master 结构断言
    # 保证 (_inner 同一个 grid ⇒ 共用一个 scrollregion ⇒ 一起滚)。
    # 保留两个位置是为了: 将来若改成不 withdraw, 这里会自动开始真的验滚动耦合。
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

    section("SnapshotProgressDialog — 标题显示 rel_path 且按像素限宽")
    d3 = SnapshotProgressDialog(root, [make_info(title='a.mp4', rel_path='./A/B'),
                                      make_info(title='a.mp4', rel_path='./C')],
                                tempfile.mkdtemp())
    root.update_idletasks()
    tl = d3._row_title_labels
    check("第 1 行标题含子文件夹路径", tl[0].cget('text') == './A/B/a.mp4',
          f"got={tl[0].cget('text')!r}")
    check("第 2 行标题是 ./C/a.mp4", tl[1].cget('text') == './C/a.mp4',
          f"got={tl[1].cget('text')!r}")
    width_short = d3._inner.winfo_reqwidth()

    d4 = SnapshotProgressDialog(
        root, [make_info(title='很长的批次/子目录/成片_final_v2_1080p.mp4',
                         rel_path='./2026/10/23')], tempfile.mkdtemp())
    root.update_idletasks()
    lbl4 = d4._row_title_labels[0]
    check("超长路径被截断", lbl4.cget('text') != lbl4._full_text,
          f"shown_len={len(lbl4.cget('text'))}")
    check("截断后以 ... 结尾", lbl4.cget('text').endswith('...'))
    check("_full_text 存的是完整值", lbl4._full_text.endswith('.mp4'))
    check("_truncated 标记为 True", lbl4._truncated is True)
    # 列宽: A/B 对比 —— 超长路径不应把整表撑宽。
    # 不用 grid_bbox (它报的是分配区宽度, 不随文本变化, 测不出任何东西)。
    # truncate_text 留 10px 余量扣 padx+bd, 实测标题 label 的 reqwidth 会是
    # 250~251, 所以断言总宽差 <= 2px 而不是精确相等。
    width_long = d4._inner.winfo_reqwidth()
    check("超长路径不撑宽标题列 (总宽差 <= 2px)",
          abs(width_long - width_short) <= 2,
          f"short={width_short} long={width_long}")

    section("SnapshotProgressDialog — 短路径不标 _truncated")
    check("未截断时 _truncated=False", tl[0]._truncated is False,
          f"got={tl[0]._truncated!r}")
    check("未截断时 shown == full", tl[0].cget('text') == tl[0]._full_text)

    d3.top.destroy(); d4.top.destroy()

    section("单位规则 — cell 不带单位, N/A 不受影响 (Review Focus #3)")
    from video_checker import parse_video_info

    def _fake_fmt(**kw):
        d = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                          "r_frame_rate": "30/1"}], "format": {"format_name": "mp4"}}
        d["streams"][0].update(kw)
        return d

    info = parse_video_info(_fake_fmt(bit_rate='32000000', codec_name='h264'),
                            '/x/a.mp4', '/x', 30000.0, 48.0)
    check("frame_rate 不带 fps 后缀", info.frame_rate == '30.00',
          f"got={info.frame_rate!r}")
    check("无音频流时 audio_sample_rate = N/A", info.audio_sample_rate == 'N/A',
          f"got={info.audio_sample_rate!r}")
    check("排序用的 frame_rate_fps 仍是数值", info.frame_rate_fps == 30.0,
          f"got={info.frame_rate_fps!r}")

    d_audio = _fake_fmt(bit_rate='32000000', codec_name='h264')
    d_audio["streams"].append({"codec_type": "audio", "codec_name": "aac",
                               "channels": 2, "sample_rate": "48000"})
    info2 = parse_video_info(d_audio, '/x/a.mp4', '/x', 30000.0, 48.0)
    check("audio_sample_rate 不带 kHz 后缀", info2.audio_sample_rate == '48.0',
          f"got={info2.audio_sample_rate!r}")
    check("排序用的 sample_rate_khz 仍是数值", info2.sample_rate_khz == 48.0,
          f"got={info2.sample_rate_khz!r}")

    d_bad = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                          "r_frame_rate": "0/0", "codec_name": "h264"}],
             "format": {"format_name": "mp4"}}
    info3 = parse_video_info(d_bad, '/x/a.mp4', '/x', 30000.0, 48.0)
    check("r_frame_rate=0/0 → N/A (不是 'nan')", info3.frame_rate == 'N/A',
          f"got={info3.frame_rate!r}")
    # 帧率未知时 frame_rate_fps 保持 0.0: 排序不受影响,
    # parse_time_token(帧号) 仍会正确地拒绝未知帧率。
    check("r_frame_rate=0/0 → frame_rate_fps 仍是 0.0", info3.frame_rate_fps == 0.0,
          f"got={info3.frame_rate_fps!r}")
    for bad_rate in ('0/1', 'abc', '30', ''):
        info_b = parse_video_info(_fake_fmt(r_frame_rate=bad_rate),
                                  '/x/a.mp4', '/x', 30000.0, 48.0)
        check(f"r_frame_rate={bad_rate!r} → N/A", info_b.frame_rate == 'N/A',
              f"got={info_b.frame_rate!r}")
    # 正常帧率不受 N/A 分支影响
    for good_rate, want_fps, want_txt in (('30/1', 30.0, '30.00'),
                                          ('30000/1001', 30000 / 1001, '29.97')):
        info_g = parse_video_info(_fake_fmt(r_frame_rate=good_rate),
                                  '/x/a.mp4', '/x', 30000.0, 48.0)
        check(f"r_frame_rate={good_rate} → {want_txt}", info_g.frame_rate == want_txt,
              f"got={info_g.frame_rate!r}")
        check(f"r_frame_rate={good_rate} → fps={want_fps:.4f}",
              abs(info_g.frame_rate_fps - want_fps) < 1e-6,
              f"got={info_g.frame_rate_fps!r}")

    section("headers — 表头带单位")
    _hdr_ok = "'frame_rate': ('帧率(fps)'" in app_widgets_body()
    check("帧率表头含 (fps)", _hdr_ok,
          "" if _hdr_ok else "headers 里帧率列仍是「帧率」")
    check("采样率表头含 (kHz)",
          "'audio_sample_rate': ('采样率(kHz)'" in app_widgets_body())
    check("码率表头含 (kbps)",
          "'bitrate': ('码率(kbps)'" in app_widgets_body())

    section("_format_cell — spec 列 (Review Focus #2)")
    spec_cases = [
        (dict(container_format='mp4', video_codec='h264', audio_codec='aac'),
         'mp4/h264/aac'),
        (dict(container_format='matroska', video_codec='h264', audio_codec='aac'),
         'matroska/h264/aac'),
        (dict(container_format='mp4', video_codec='h264', audio_codec='N/A'),
         'mp4/h264/N/A'),
        (dict(container_format='mp4', video_codec='N/A', audio_codec='N/A'),
         'mp4/N/A/N/A'),
    ]
    for kw, want in spec_cases:
        got = VideoGridView._format_cell(make_info(**kw), 'spec')
        check(f"spec{kw} → {want!r}", got == want, f"got={got!r}")

    section("列集合 — 10 列 + 四个可排序列仍存在")
    import re
    from video_checker import _SORTABLE_COLUMNS
    app_body = app_widgets_body()
    header_keys = re.findall(r"'(\w+)':\s*\('", app_body)
    check("headers 恰好 10 列", len(header_keys) == 10, f"got={len(header_keys)}: {header_keys}")
    for removed in ('container_format', 'video_codec', 'audio_codec'):
        check(f"headers 已移除 {removed}", removed not in header_keys)
    check("headers 含 spec", 'spec' in header_keys)
    for sortable in sorted(_SORTABLE_COLUMNS):
        check(f"可排序列 {sortable} 仍在 headers 中", sortable in header_keys)
    check("死代码 columns 元组已删除 (_create_widgets 里不该再有 columns = ( 元组)",
          not re.search(r'^\s*columns\s*=\s*\(', app_body, re.M))

    section("VideoGridView._truncate_text — 委托且行为一致")
    grid = VideoGridView(ttk.Frame(root), {'title': ('标题', 250)})
    check("方法委托给模块级 truncate_text",
          grid._truncate_text('a' * 200, 100)
          == truncate_text('a' * 200, 100, grid._cell_font))
    check("短文本不受影响", grid._truncate_text('a.mp4', 250) == 'a.mp4')

    section("mark_done — PhotoImage 只留 display (Review Focus #2/#5)")
    d5 = SnapshotProgressDialog(root, [make_info()], tempfile.mkdtemp())
    # 时间点 cell 是起跑时 _update_table() 才建的 (构造时只有序号/标题列),
    # 这里补建, 让 _cells[0][0] 存在。
    d5._update_table(d5._time_tokens)
    root.update_idletasks()
    # 用 tkinter 造一张 640x360 PNG (不依赖 ffmpeg)
    big = tk.PhotoImage(width=640, height=360)
    big.put('red', to=(0, 0, 639, 359))
    png = os.path.join(tempfile.mkdtemp(), 'frame.png')
    big.write(png, format='png')
    del big

    d5._state = 'running'
    # Tk 的 image table 才是真正的持有者: 只看 _photo_refs 长度的话, 把 640px
    # 原图藏进别的属性/模块缓存/闭包都能照样全绿。所以直接对比 image table。
    before = set(root.tk.call('image', 'names'))
    d5.mark_done(0, 0, png)
    after = set(root.tk.call('image', 'names'))
    new = after - before
    sizes = {n: (root.tk.call('image', 'width', n),
                 root.tk.call('image', 'height', n)) for n in new}
    check("640x360 原图已从 Tk image table 释放",
          all(s != (640, 360) for s in sizes.values()),
          f"残留 640x360: {[n for n, s in sizes.items() if s == (640, 360)]}")
    check("image table 里留下的是 320x180 缩略图",
          sizes.get(d5._photo_refs[0].name) == (320, 180),
          f"name={d5._photo_refs[0].name} sizes={sizes}")
    check("_photo_refs 每 cell 恰好 1 项 (原来 2 项)",
          len(d5._photo_refs) == 1, f"len={len(d5._photo_refs)}")
    check("留下的是 320px display 不是 640px 原图",
          d5._photo_refs[0].width() == 320, f"w={d5._photo_refs[0].width()}")
    check("cell 挂的正是 _photo_refs[0] 这张图",
          d5._cells[0][0].cget('image') == d5._photo_refs[0].name,
          f"cell={d5._cells[0][0].cget('image')!r} "
          f"ref={d5._photo_refs[0].name!r}")
    d5.top.destroy()

    section("mark_failed — 无残留图片 (Review Focus #5)")
    d6 = SnapshotProgressDialog(root, [make_info()], tempfile.mkdtemp())
    d6._update_table(d6._time_tokens)
    d6._state = 'running'
    d6.mark_failed(0, 0)
    check("失败后 _photo_refs 为空", len(d6._photo_refs) == 0,
          f"len={len(d6._photo_refs)}")
    check("失败 cell 无残留 image", d6._cells[0][0].cget('image') == '',
          f"got={d6._cells[0][0].cget('image')!r}")
    check("失败计数 +1", d6._failed_count == 1, f"got={d6._failed_count}")
    d6.top.destroy()

    section("状态栏常驻计数 (Review Focus #4)")
    from tkinterdnd2 import TkinterDnD
    from video_checker import VideoCheckerApp
    # 必须用 TkinterDnD.Tk(): VideoGridView.bind_drop 会调 drop_target_register,
    # 普通 tk.Tk() 上会 AttributeError。
    app_root = TkinterDnD.Tk()
    app_root.withdraw()
    app = VideoCheckerApp(app_root)
    app_root.update_idletasks()
    check("初始计数全 0",
          app.count_var.get() == '共 0 · 达标 0 · 不达标 0',
          f"got={app.count_var.get()!r}")

    app._add_result(make_info(title='p.mp4', is_passing=True))
    app._add_result(make_info(title='f.mp4', is_passing=False))
    app._add_result(make_info(title='p2.mp4', is_passing=True))
    app_root.update_idletasks()
    check("插入 3 条后计数正确",
          app.count_var.get() == '共 3 · 达标 2 · 不达标 1',
          f"got={app.count_var.get()!r}")

    app._clear_records()
    app_root.update_idletasks()
    check("清除记录后归零",
          app.count_var.get() == '共 0 · 达标 0 · 不达标 0',
          f"got={app.count_var.get()!r}")
    check("状态文案为「已清除记录」", app.status_var.get() == '已清除记录',
          f"got={app.status_var.get()!r}")

    section("状态栏 — 0 文件完成时计数归零 (Review Focus #4)")
    app.total_files = 0
    app._scan_complete()
    app_root.update_idletasks()
    check("0 文件后计数不残留",
          app.count_var.get() == '共 0 · 达标 0 · 不达标 0',
          f"got={app.count_var.get()!r}")
    check("完成文案简化成「检测完成」",
          app.status_var.get() == '检测完成', f"got={app.status_var.get()!r}")

    # brief 漏掉的路径: _start_scan / _on_file_drop 也 clear 了 video_results,
    # 但 brief 没让它们重置 _passed_count —— 扫第二轮会累加上一轮的达标数,
    # 状态栏出现「达标 188 · 不达标 -178」。真跑这两个方法要起线程调 ffprobe,
    # 太重; 退一步锚源码: clear() 之后必须紧跟计数归零。
    for meth in ('_start_scan', '_on_file_drop'):
        body = method_body(meth)
        at = body.index('self.video_results.clear()')
        window = body[at:at + 320]
        ok = ('self._passed_count = 0' in window
              and 'self._refresh_counts()' in window)
        # check() 无条件打印 detail, 通过时别塞一句像报错的文案
        check(f"{meth} 清空结果时同步重置计数", ok,
              "" if ok else "clear() 之后的窗口里没找到 _passed_count = 0 / _refresh_counts()")
    app_root.destroy()

    section("Tooltip — 只在截断时显示")
    from video_checker import Tooltip

    class _FakeCell:
        def __init__(self, full, truncated):
            self._full_text = full
            self._truncated = truncated

    class _FakeEv:
        def __init__(self, widget):
            self.widget = widget

    tip = Tooltip(root)
    plain = _FakeCell('./a.mp4', False)
    tip.on_motion(_FakeEv(plain))
    root.update_idletasks()
    check("未截断的 cell 不弹 tooltip", tip.win is None, f"got={tip.win!r}")

    cut = _FakeCell('./很长的路径/子目录/文件.mp4', True)
    tip.on_motion(_FakeEv(cut))
    root.update_idletasks()
    check("截断的 cell 弹 tooltip", tip.win is not None)
    if tip.win is not None:
        texts = [w.cget('text') for w in tip.win.winfo_children()
                 if isinstance(w, tk.Label)]
        check("tooltip 显示完整值", texts and texts[0] == cut._full_text,
              f"got={texts}")
        check("tooltip 覆写重定向 (不抢焦点)", bool(tip.win.overrideredirect()))

    tip.on_motion(_FakeEv(cut))     # 同一 cell 再来一次
    root.update_idletasks()
    first_win = tip.win
    tip.on_motion(_FakeEv(cut))
    root.update_idletasks()
    check("同一 cell 重复 hover 不重建窗口", tip.win is first_win)

    tip.on_motion(_FakeEv(plain))   # 移到未截断 cell
    root.update_idletasks()
    check("移到未截断 cell 后关闭", tip.win is None)

    section("网格 cell 带 _truncated 标记")
    g = VideoGridView(ttk.Frame(root), {'title': ('标题', 60)})
    info_cut = make_info(title='x' * 100 + '.mp4', rel_path='./deep/dir')
    info_short = make_info(title='a.mp4', rel_path='./')
    g.add_row(info_cut)      # → pool slot 0
    g.add_row(info_short)    # → pool slot 1
    root.update_idletasks()
    slot0 = g._widget_pool[0]._cells[0][1]    # (key, label) 对, 取 title
    slot1 = g._widget_pool[1]._cells[0][1]
    check("长路径 cell 标了 _truncated", slot0._truncated is True,
          f"text={slot0.cget('text')!r}")
    check("短路径 cell 未标截断", slot1._truncated is False,
          f"text={slot1.cget('text')!r}")
    check("cell 存了 _full_text", slot0._full_text.endswith('.mp4'))
    check("cell 绑了 <Motion>", bool(slot0.bind('<Motion>')))
    check("cell 绑了 <Leave>", bool(slot0.bind('<Leave>')))

    section("Tooltip — <Leave> 延迟关闭 + 窗口复用")
    tip3 = Tooltip(root)

    class _TruncCell:
        """全都被截断的假 cell —— 这里只测 Tooltip 的窗口行为。"""
        def __init__(self, text):
            self._full_text = text
            self._truncated = True

    ca = _TruncCell('./很长的路径/甲.mp4')
    cb = _TruncCell('./很长的路径/乙.mp4')

    # (1) 截断 cell A → B: 复用窗口, 只换文本。
    #     不复用的话每次跨边界都要销毁+新建 Toplevel (实测 ~4.4ms/次),
    #     扫 20 行就是几百毫秒的纯窗口创建开销, 且看着闪。
    tip3.on_motion(_FakeEv(ca))
    root.update_idletasks()
    check("tooltip 已弹出", tip3.win is not None)
    win_ab = tip3.win
    tip3.on_leave(_FakeEv(ca))
    tip3.on_motion(_FakeEv(cb))
    root.update_idletasks()
    check("A→B 复用窗口 (不销毁重建)", tip3.win is win_ab,
          f"before={win_ab!r} after={tip3.win!r}")
    t3 = ([w.cget('text') for w in tip3.win.winfo_children()
           if isinstance(w, tk.Label)] if tip3.win is not None else [])
    check("A→B 后显示的是 B 的完整值", t3 and t3[0] == cb._full_text,
          f"got={t3}")

    # (2) 鼠标离开到没有绑定的 widget (表头/滚动条/状态栏): 之后不再有
    #     <Motion>, on_motion 不跑 —— 只能靠 <Leave> 关闭, 否则浮窗带着旧文本
    #     一直挂在鼠标附近, 看着像是属于鼠标当前位置, 会误导。
    tip3.on_leave(_FakeEv(cb))
    root.update_idletasks()
    check("Leave 后 (无后续 Motion) tooltip 关闭", tip3.win is None,
          f"got={tip3.win!r}")

    # (3) 同 cell 抖动 (边界上进出): leave 后又回同一 cell。
    #     延迟关闭独占的收益 —— 直接 hide() 会销毁重建同一个东西, 白闪。
    tip3.on_motion(_FakeEv(ca))
    root.update_idletasks()
    win_c = tip3.win
    tip3.on_leave(_FakeEv(ca))
    tip3.on_motion(_FakeEv(ca))
    root.update_idletasks()
    check("同 cell 抖动不销毁重建", tip3.win is win_c,
          f"before={win_c!r} after={tip3.win!r}")
    tip3.hide()

    section("Tooltip — 真实事件派发 (不是查绑定字符串)")
    # 上面几条是直接调 Tooltip 方法, 只能证明方法本身对, 证明不了 cell 上
    # 的绑定真的把事件送到了它。这里派发真实 <Motion>/<Leave>。
    # 注意: bind() 非空 ≠ handler 是对的 —— 绑成空 lambda 照样绿 (前车之鉴),
    # 所以必须看事件派发后的实际效果。
    g2 = VideoGridView(ttk.Frame(root), {'title': ('标题', 60)})
    g2.add_row(make_info(title='x' * 100 + '.mp4', rel_path='./deep/dir'))
    g2.add_row(make_info(title='y' * 100 + '.mp4', rel_path='./other/dir'))
    g2.add_row(make_info(title='a.mp4', rel_path='./'))
    root.update_idletasks()
    r_cut = g2._widget_pool[0]._cells[0][1]
    r_cut2 = g2._widget_pool[1]._cells[0][1]
    r_short = g2._widget_pool[2]._cells[0][1]
    g2.tooltip.hide()

    r_short.event_generate('<Motion>', x=5, y=5, when='now')
    root.update_idletasks()
    check("真实 <Motion> 到未截断 cell → 不弹", g2.tooltip.win is None,
          f"got={g2.tooltip.win!r}")

    r_cut.event_generate('<Motion>', x=5, y=5, when='now')
    root.update_idletasks()
    check("真实 <Motion> 到截断 cell → 弹出", g2.tooltip.win is not None)
    if g2.tooltip.win is not None:
        lt = [w.cget('text') for w in g2.tooltip.win.winfo_children()
              if isinstance(w, tk.Label)]
        check("真实事件弹出的内容是完整值 (不是截断值)",
              lt and lt[0] == r_cut._full_text, f"got={lt}")

    r_cut.event_generate('<Leave>', when='now')
    root.update_idletasks()
    check("真实 <Leave> 后关闭 (不残留陈旧浮窗)", g2.tooltip.win is None,
          f"got={g2.tooltip.win!r}")

    r_cut.event_generate('<Motion>', x=5, y=5, when='now')
    root.update_idletasks()
    w_live = g2.tooltip.win
    r_cut.event_generate('<Leave>', when='now')
    r_cut2.event_generate('<Motion>', x=5, y=5, when='now')
    root.update_idletasks()
    check("真实事件 cell→cell 复用窗口", g2.tooltip.win is w_live,
          f"before={w_live!r} after={g2.tooltip.win!r}")
    lt2 = ([w.cget('text') for w in g2.tooltip.win.winfo_children()
            if isinstance(w, tk.Label)] if g2.tooltip.win is not None else [])
    check("真实事件 cell→cell 后内容是第二个 cell 的完整值",
          lt2 and lt2[0] == r_cut2._full_text, f"got={lt2}")
    g2.tooltip.hide()

    section("clear 时关掉悬浮中的 tooltip")
    tip2 = Tooltip(root)
    g.tooltip = tip2
    g.tooltip.show(slot0)
    root.update_idletasks()
    check("tooltip 已弹出", tip2.win is not None)
    g.clear()
    root.update_idletasks()
    check("clear 后 tooltip 已关闭 (不残留旧文本)",
          tip2.win is None, f"got={tip2.win!r}")

    root.destroy()

    print(f"\n{'FAIL' if _failures else 'PASS'}: {len(_failures)} failure(s)")
    sys.exit(1 if _failures else 0)


if __name__ == "__main__":
    main()