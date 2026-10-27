"""Qt 表格模型的行为断言。

对照 docs/产品文档.md 的表格规格 (§4.2) 与着色规则 (§3.3)。
跑法: python docs/superpowers/specs/2026-10-27-qt-table-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication

# QApplication 必须先于 video_qt 建好: video_qt 的 ui_font() 会拷贝
# **当前 app 的默认字体**, app 还不存在时字体族留空 → 字宽度量跟真机对不上
# (cocoa 下 '码率(kbps)' 会从 48px 变成 67px)。断言里量宽度用的
# QFontMetrics(ui_font()) 必须跟模型里的 QFontMetrics(ui_font()) 是同一套度量。
#
# 注意: 用 ui_font() 而不是 `from video_qt import UI_FONT` —— 后者是**快照**
# 语义 (import 那一刻求值一次, 之后 app 建好了也不刷新)。
app = QApplication.instance() or QApplication(sys.argv)

from video_qt import ui_font

_failures = []


def check(name, condition, detail=""):
    mark = "✓" if condition else "✗"
    line = f"  {mark} {name}" + (f" — {detail}" if detail else "")
    print(line)
    if not condition:
        _failures.append(line)


def section(title):
    print(f"\n=== {title} ===")


def no_raise(fn):
    """调 fn; 抛异常就返回 'EXC:<类型>' 而不是把脚本整个炸掉。

    用来断言「不该抛异常」的地方 —— 越界/过期 index 这类。
    """
    try:
        return fn()
    except Exception as exc:
        return f'EXC:{type(exc).__name__}'


def main():
    app = QApplication.instance() or QApplication(sys.argv)

    from video_qt import VIDEO_COLUMNS, VideoTableModel
    from video_core import VideoInfo

    def mk(title='a.mp4', passing=True, low_br=False, low_sr=False, bitrate=None):
        if bitrate is None:
            bitrate = 1000.0 if low_br else 32000.0
        return VideoInfo(
            rel_path='./', title=title, resolution='1920x1080',
            frame_rate='30.00', bitrate_kbps=bitrate,
            video_codec='h264', audio_codec='aac', audio_channels='2',
            audio_sample_rate='48.0', duration='00:01:00', file_size='1.0 GB',
            is_passing=passing, sample_rate_passing=not low_sr,
            bitrate_std=30000.0, sample_rate_std=48.0,
            full_path=f'/x/{title}', container_format='mp4',
            frame_rate_fps=30.0, sample_rate_khz=48.0)

    section("列集合")
    check("11 列 (序号 + 10 数据列)", len(VIDEO_COLUMNS) == 11,
          f"got={len(VIDEO_COLUMNS)}")
    check("第 0 列是序号列 (key=None)", VIDEO_COLUMNS[0].key is None)
    check("序号列宽 45", VIDEO_COLUMNS[0].width == 45)
    check("序号列不可排序", not VIDEO_COLUMNS[0].sortable)
    keys = [c.key for c in VIDEO_COLUMNS[1:]]
    check("数据列 key 与产品文档一致",
          keys == ['title', 'resolution', 'frame_rate', 'bitrate', 'spec',
                   'audio_channels', 'audio_sample_rate', 'duration',
                   'file_size', 'result'], f"got={keys}")
    check("只有 title 左对齐, 其余居中",
          [c.align for c in VIDEO_COLUMNS[1:]]
          == ['w'] + ['center'] * 9,
          f"got={[c.align for c in VIDEO_COLUMNS[1:]]}")
    check("可排序列 4 个",
          sorted(c.key for c in VIDEO_COLUMNS if c.sortable)
          == ['audio_sample_rate', 'bitrate', 'frame_rate', 'result'],
          f"got={sorted(c.key for c in VIDEO_COLUMNS if c.sortable)}")
    for removed in ('container_format', 'video_codec', 'audio_codec'):
        check(f"数据列里没有 {removed} (合进了 spec 列)",
              removed not in keys, f"got={keys}")
    check("数据列含 spec", 'spec' in keys, f"got={keys}")

    section("表头文字带单位")
    # 不写死「显示出来的那串字符」—— 列宽不够时表头会被截断, 换平台/字体
    # 截断点就变了 (硬写 '帧率(fps)' 会跨平台假红)。改成两条:
    #   ① 列规格里的表头带单位 (稳定, 不受截断影响)
    #   ② 显示出来的表头是那段文字本身, 或者是它的截断前缀
    mh = VideoTableModel(VIDEO_COLUMNS)
    for want, section_no in (('帧率(fps)', 3), ('码率(kbps)', 4),
                             ('采样率(kHz)', 7)):
        spec_text = VIDEO_COLUMNS[section_no].header
        check(f"第 {section_no} 列的列规格带单位 {want!r}", spec_text == want,
              f"got={spec_text!r}")
        got = mh.headerData(section_no, Qt.Horizontal, Qt.DisplayRole)
        shown_ok = (got == want) or (
            got.endswith('...') and want.startswith(got[:-3]))
        check(f"第 {section_no} 列显示的表头就是带单位的那段文字 (或它的截断)",
              shown_ok, f"got={got!r}")

    section("单元格不带单位 (N/A 不受影响)")
    from video_core import parse_video_info

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
    for bad_rate in ('0/1', 'abc', '30', '', None):
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

    # 显示层: 单元格里的值跟解析出来的一致 —— 单位只写在表头上
    mu = VideoTableModel(VIDEO_COLUMNS)
    for inf in (info, info2, info3):
        mu.append_result(inf)
    app.processEvents()
    check("帧率格显示 '30.00' (不带 fps 单位)",
          mu.data(mu.index(0, 3), Qt.DisplayRole) == '30.00',
          f"got={mu.data(mu.index(0, 3), Qt.DisplayRole)!r}")
    check("码率格显示 '32000' (不带 kbps 单位)",
          mu.data(mu.index(0, 4), Qt.DisplayRole) == '32000',
          f"got={mu.data(mu.index(0, 4), Qt.DisplayRole)!r}")
    check("采样率格显示 '48.0' (不带 kHz 单位)",
          mu.data(mu.index(1, 7), Qt.DisplayRole) == '48.0',
          f"got={mu.data(mu.index(1, 7), Qt.DisplayRole)!r}")
    check("无音频流时采样率格显示 'N/A'",
          mu.data(mu.index(0, 7), Qt.DisplayRole) == 'N/A',
          f"got={mu.data(mu.index(0, 7), Qt.DisplayRole)!r}")
    check("未知帧率显示 'N/A'",
          mu.data(mu.index(2, 3), Qt.DisplayRole) == 'N/A',
          f"got={mu.data(mu.index(2, 3), Qt.DisplayRole)!r}")

    section("title 列委托 format_title")
    from video_core import format_title
    rel_cases = [
        ('./',        'a.mp4', './a.mp4'),
        ('./A',       'a.mp4', './A/a.mp4'),
        ('./A/B',     'a.mp4', './A/B/a.mp4'),
        ('./A/B/',    'a.mp4', './A/B/a.mp4'),
        ('',          'a.mp4', './a.mp4'),
        ('D:\\videos\\a.mp4',  'a.mp4', 'D:\\videos\\a.mp4'),
        ('\\\\srv\\share\\a.mp4', 'a.mp4', '\\\\srv\\share\\a.mp4'),
    ]
    mt = VideoTableModel(VIDEO_COLUMNS)
    for rel, title, _want in rel_cases:
        inf = mk(title=title)
        inf.rel_path = rel
        mt.append_result(inf)
    app.processEvents()
    for r, (rel, title, want) in enumerate(rel_cases):
        got = mt.data(mt.index(r, 1), Qt.DisplayRole)
        check(f"title 格 rel_path={rel!r} → {want!r}", got == want,
              f"got={got!r}")
        check(f"title 格 == format_title(info) (不是各拼各的)",
              got == format_title(mt.sorted_rows[r]), f"got={got!r}")

    section("spec 列格式")
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
    ms = VideoTableModel(VIDEO_COLUMNS)
    for kw, _want in spec_cases:
        inf = mk('spec.mp4')
        for k, v in kw.items():
            setattr(inf, k, v)
        ms.append_result(inf)
    app.processEvents()
    for r, (kw, want) in enumerate(spec_cases):
        got = ms.data(ms.index(r, 5), Qt.DisplayRole)
        check(f"spec{kw} → {want!r}", got == want, f"got={got!r}")

    section("着色矩阵")
    m = VideoTableModel(VIDEO_COLUMNS)
    m.append_result(mk('ok.mp4', passing=True))
    m.append_result(mk('bad.mp4', passing=False, low_br=True, low_sr=True))
    m.append_result(mk('half.mp4', passing=False, low_br=True, low_sr=False))
    app.processEvents()

    def fg_at(row, col):
        idx = m.index(row, col)
        brush = m.data(idx, Qt.ForegroundRole)
        return brush.color().name() if brush else None

    check("达标行全绿",
          all(fg_at(0, c) == '#228b22' for c in range(1, 11)),
          f"got={[fg_at(0, c) for c in range(1, 11)]}")
    red_cols = {c: fg_at(1, c) for c in range(1, 11)}
    check("不达标行只有 title/result/bitrate/audio_sample_rate 红",
          [k for k, v in red_cols.items() if v == '#dc143c']
          == [1, 4, 7, 10],
          f"got={red_cols}")
    check("序号列永远黑色", fg_at(1, 0) == '#000000', f"got={fg_at(1, 0)}")
    check("只码率不达标 → title/result/bitrate 红",
          [c for c in range(1, 11) if fg_at(2, c) == '#dc143c'] == [1, 4, 10],
          f"got={[c for c in range(1, 11) if fg_at(2, c) == '#dc143c']}")

    section("着色常量只有一个来源 (界面层不另抄一份字面量)")
    # 原断言: VideoGridView.FG_PASS 就是 core 的 FG_PASS (Tk 网格类属性
    # 不能各抄一份颜色字面量)。Qt 版没有网格类, 着色走 core 的 _cell_fg ——
    # 同一个不变量的 Qt 形态: 界面层不持有自己的颜色常量。
    import video_core
    import video_qt
    check("video_qt 没有自己的 FG_PASS (不另抄一份)",
          not hasattr(video_qt, 'FG_PASS'),
          f"got={getattr(video_qt, 'FG_PASS', None)!r}")
    check("video_qt 没有自己的 FG_FAIL (不另抄一份)",
          not hasattr(video_qt, 'FG_FAIL'),
          f"got={getattr(video_qt, 'FG_FAIL', None)!r}")
    with open(video_qt.__file__, encoding='utf-8') as fh:
        qt_src = fh.read().lower()
    check("video_qt.py 里没有硬抄的 '#228b22' / '#dc143c' 字面量",
          '#228b22' not in qt_src and '#dc143c' not in qt_src)
    check("core 的 FG_PASS 是绿色", video_core.FG_PASS.lower() == '#228b22',
          f"got={video_core.FG_PASS!r}")
    check("core 的 FG_FAIL 是红色", video_core.FG_FAIL.lower() == '#dc143c',
          f"got={video_core.FG_FAIL!r}")
    check("达标行渲染的颜色 == core.FG_PASS",
          fg_at(0, 1).lower() == video_core.FG_PASS.lower(),
          f"got={fg_at(0, 1)!r}")
    check("不达标行渲染的颜色 == core.FG_FAIL",
          fg_at(1, 1).lower() == video_core.FG_FAIL.lower(),
          f"got={fg_at(1, 1)!r}")

    section("单元格对齐 (Qt.TextAlignmentRole)")
    ma = VideoTableModel(VIDEO_COLUMNS)
    ma.append_result(mk('a.mp4'))

    def align_at(col):
        return ma.data(ma.index(0, col), Qt.TextAlignmentRole)

    check("title 列左对齐",
          align_at(1) == int(Qt.AlignLeft | Qt.AlignVCenter),
          f"got={align_at(1)}")
    check("其余数据列全部居中",
          all(align_at(c) == int(Qt.AlignCenter) for c in range(2, 11)),
          f"got={[align_at(c) for c in range(2, 11)]}")
    check("序号列居中", align_at(0) == int(Qt.AlignCenter), f"got={align_at(0)}")

    section("列宽变化 → 表头重算")
    mw = VideoTableModel(VIDEO_COLUMNS)
    mw.append_result(mk('a.mp4'))
    before_hdr = mw.headerData(4, Qt.Horizontal, Qt.DisplayRole)
    check("变窄前表头是完整文字", before_hdr == '码率(kbps)', f"got={before_hdr!r}")
    events = []
    mw.headerDataChanged.connect(lambda *args: events.append(args))
    mw.set_column_width(4, 30)
    check("set_column_width 发 headerDataChanged(Horizontal, 4, 4)",
          events == [(Qt.Horizontal, 4, 4)], f"got={events}")
    after_hdr = mw.headerData(4, Qt.Horizontal, Qt.DisplayRole)
    # 截断到几行取决于字体度量 (CJK 字宽各平台不同), 所以不写死确切字符串,
    # 改成断言「按 truncate_text 的口径被重新截断了」:
    #   ① 结果以 '...' 结尾
    #   ② 结果是原文的**前缀** (截断), 不是拼接 (拼接会得到 '码率(kbps)...')
    #   ③ 结果装得进新预算 (col_width - 10)
    #   ④ 完整文字在新预算下装不下 → 截断是必须的 (排除「本来就不用截」)
    fm = QFontMetrics(ui_font())
    budget = 30 - 10          # truncate_text 的口径: col_width - 10
    check("变窄后表头被重新截断 (新预算下的原文前缀+省略号)",
          after_hdr.endswith('...')
          and before_hdr.startswith(after_hdr[:-3])       # 是截断而非拼接
          and fm.horizontalAdvance(after_hdr) <= budget   # 结果装得进新预算
          and fm.horizontalAdvance(before_hdr) > budget,  # 完整文字在新预算下装不下 → 截断是必须的
          f"got={after_hdr!r}")
    mw.set_column_width(4, 20)
    hdr20 = mw.headerData(4, Qt.Horizontal, Qt.DisplayRole)
    mw.set_column_width(4, 5)
    check("列宽 5 被夹到 20 (与直接设 20 的表头一致)",
          mw.headerData(4, Qt.Horizontal, Qt.DisplayRole) == hdr20,
          f"got={mw.headerData(4, Qt.Horizontal, Qt.DisplayRole)!r} "
          f"vs {hdr20!r}")
    check("越界列号被忽略 (不塞野列)",
          no_raise(lambda: mw.set_column_width(99, 50)) is None
          and 99 not in mw._widths,
          f"got={sorted(mw._widths)}")

    section("列宽变化 → 单元格重截断")
    mc = VideoTableModel(VIDEO_COLUMNS)
    mc.append_result(mk('a.mp4'))
    app.processEvents()
    fm = QFontMetrics(ui_font())
    cell_budget = 30 - 10      # truncate_text 的口径: col_width - 10
    cell_before = mc.data(mc.index(0, 4), Qt.DisplayRole)
    dc_events = []
    mc.dataChanged.connect(lambda *args: dc_events.append(args))
    mc.set_column_width(4, 30)
    cell_after = mc.data(mc.index(0, 4), Qt.DisplayRole)
    check("变窄后单元格被重新截断 (新预算下的原文前缀+省略号)",
          cell_after.endswith('...')
          and cell_before.startswith(cell_after[:-3])     # 是截断而非拼接
          and fm.horizontalAdvance(cell_after) <= cell_budget  # 结果装得进新预算
          and fm.horizontalAdvance(cell_before) > cell_budget,  # 完整文字装不下 → 截断是必须的
          f"got={cell_after!r}")
    check("set_column_width 发 dataChanged (含 DisplayRole)",
          len(dc_events) == 1
          and dc_events[0][0].row() == 0 and dc_events[0][0].column() == 4
          and Qt.DisplayRole in dc_events[0][2],
          f"got={dc_events}")

    section("截断口径与 video_core.truncate_text 一致")
    from video_core import truncate_text
    mt2 = VideoTableModel(VIDEO_COLUMNS)
    long_info = mk('x' * 200 + '.mp4')
    mt2.append_result(long_info)
    app.processEvents()
    for col in (1, 5):                      # title / spec
        want = truncate_text(mt2._full_text(col, long_info),
                             mt2._widths[col], mt2._measure)
        got = mt2.data(mt2.index(0, col), Qt.DisplayRole)
        check(f"第 {col} 列显示的文本 == truncate_text(完整文本, 列宽)",
              got == want, f"got={got!r} want={want!r}")
    check("短文本原样返回 (不截断)",
          mt2.data(mt2.index(0, 5), Qt.DisplayRole) == 'mp4/h264/aac',
          f"got={mt2.data(mt2.index(0, 5), Qt.DisplayRole)!r}")

    section("序号列 = 排序后的新位置")
    m2 = VideoTableModel(VIDEO_COLUMNS)
    for i, br in enumerate([50000, 40000, 30000, 20000, 10000]):
        m2.append_result(mk(f'v{i}.mp4', bitrate=br))
    app.processEvents()
    check("初始按插入序",
          [m2.data(m2.index(r, 1), Qt.DisplayRole) for r in range(5)]
          == ['./v0.mp4', './v1.mp4', './v2.mp4', './v3.mp4', './v4.mp4'],
          f"got={[m2.data(m2.index(r, 1), Qt.DisplayRole) for r in range(5)]}")
    m2.on_header_clicked(4)          # bitrate 列 (索引 4) 升序
    app.processEvents()
    check("升序后按码率重排",
          [m2.data(m2.index(r, 1), Qt.DisplayRole) for r in range(5)]
          == ['./v4.mp4', './v3.mp4', './v2.mp4', './v1.mp4', './v0.mp4'],
          f"got={[m2.data(m2.index(r, 1), Qt.DisplayRole) for r in range(5)]}")
    check("序号列跟着新位置 (1..5)",
          [m2.data(m2.index(r, 0), Qt.DisplayRole) for r in range(5)]
          == ['1', '2', '3', '4', '5'],
          f"got={[m2.data(m2.index(r, 0), Qt.DisplayRole) for r in range(5)]}")

    section("排序三态")
    m3 = VideoTableModel(VIDEO_COLUMNS)
    for i in range(5):
        m3.append_result(mk(f'v{i}.mp4'))
    m3.on_header_clicked(4)          # bitrate
    check("第一次点击 → 升序", m3.sort_key == 'bitrate' and not m3.sort_desc,
          f"got={m3.sort_key} {m3.sort_desc}")
    m3.on_header_clicked(4)
    check("第二次点击 → 降序", m3.sort_key == 'bitrate' and m3.sort_desc,
          f"got={m3.sort_key} {m3.sort_desc}")
    m3.on_header_clicked(4)
    check("第三次点击 → 取消排序", m3.sort_key is None,
          f"got={m3.sort_key}")
    m3.on_header_clicked(3)          # 切到 frame_rate (索引 3)
    check("切到别的列 → 重置为升序", m3.sort_key == 'frame_rate' and not m3.sort_desc,
          f"got={m3.sort_key} {m3.sort_desc}")

    section("公开的排序入口 (is_sorted / reorder)")
    m8 = VideoTableModel(VIDEO_COLUMNS)
    for i, br in enumerate([50000, 40000, 30000, 20000, 10000]):
        m8.append_result(mk(f'v{i}.mp4', bitrate=br))
    check("初始 is_sorted() 为 False", not m8.is_sorted())
    m8.on_header_clicked(4)          # bitrate 升序
    check("排序后 is_sorted() 为 True", m8.is_sorted())
    check("排序后行序按码率升序",
          [m8.data(m8.index(r, 1), Qt.DisplayRole) for r in range(5)]
          == ['./v4.mp4', './v3.mp4', './v2.mp4', './v1.mp4', './v0.mp4'],
          f"got={[m8.data(m8.index(r, 1), Qt.DisplayRole) for r in range(5)]}")
    # 扫描中的新结果按插入序追加 (模型不自动排序), 扫完 reorder() 一次
    m8.append_result(mk('v5.mp4', bitrate=1))
    check("追加的新行留在末尾 (扫描中按插入序)",
          m8.data(m8.index(5, 1), Qt.DisplayRole) == './v5.mp4',
          f"got={m8.data(m8.index(5, 1), Qt.DisplayRole)}")
    m8.reorder()
    check("reorder() 后新行排到最前 (码率最小)",
          m8.data(m8.index(0, 1), Qt.DisplayRole) == './v5.mp4',
          f"got={m8.data(m8.index(0, 1), Qt.DisplayRole)}")
    m8.on_header_clicked(4)          # → 降序
    m8.on_header_clicked(4)          # → 取消排序
    check("取消排序后 is_sorted() 为 False", not m8.is_sorted())
    before = [m8.data(m8.index(r, 1), Qt.DisplayRole) for r in range(6)]
    m8.reorder()
    check("无排序态时 reorder() 是 no-op",
          [m8.data(m8.index(r, 1), Qt.DisplayRole) for r in range(6)] == before,
          f"got={[m8.data(m8.index(r, 1), Qt.DisplayRole) for r in range(6)]}")

    section("不可排序列点了没反应")
    m4 = VideoTableModel(VIDEO_COLUMNS)
    m4.append_result(mk('x.mp4'))
    m4.on_header_clicked(1)          # title
    check("title 列点击后无排序态", m4.sort_key is None, f"got={m4.sort_key}")
    m4.on_header_clicked(0)          # 序号
    check("序号列点击后无排序态", m4.sort_key is None, f"got={m4.sort_key}")

    section("表头点击的边界")
    m9 = VideoTableModel(VIDEO_COLUMNS)
    m9.append_result(mk('x.mp4'))
    r1 = no_raise(lambda: m9.on_header_clicked(99))
    check("越界列号点击 → 无反应 (不抛异常)", r1 is None, f"got={r1!r}")
    check("越界列号点击 → 无排序态", m9.sort_key is None, f"got={m9.sort_key}")
    r2 = no_raise(lambda: m9.on_header_clicked(-1))
    check("负数列号点击 → 无反应 (不抛异常)", r2 is None, f"got={r2!r}")
    stale = m9.index(0, 1)
    m9.clear()
    r3 = no_raise(lambda: m9.data(stale, Qt.DisplayRole))
    check("clear() 之前的 index 过期 → data() 返回 None (不抛 IndexError)",
          r3 is None, f"got={r3!r}")
    r4 = no_raise(lambda: m9.data(stale, Qt.ToolTipRole))
    check("clear() 之前的 index 过期 → ToolTipRole 也是 None (不留陈旧完整值)",
          r4 is None, f"got={r4!r}")

    section("排序态变化 → 表头要通知 (箭头刷新)")
    m10 = VideoTableModel(VIDEO_COLUMNS)
    m10.append_result(mk('x.mp4'))
    hdr_events = []
    m10.headerDataChanged.connect(lambda *args: hdr_events.append(args))
    m10.on_header_clicked(4)         # 升序
    m10.on_header_clicked(4)         # 降序
    m10.on_header_clicked(4)         # 取消排序 (这一档 _apply_sort 早退)
    check("每次点击都通知表头重写 (含取消排序那一档)",
          hdr_events == [(Qt.Horizontal, 0, 10)] * 3, f"got={hdr_events}")
    check("取消排序后表头无箭头",
          not m10.headerData(4, Qt.Horizontal, Qt.DisplayRole).endswith(('▲', '▼')),
          f"got={m10.headerData(4, Qt.Horizontal, Qt.DisplayRole)!r}")

    section("排序箭头是表头文字后缀")
    m5 = VideoTableModel(VIDEO_COLUMNS)
    m5.on_header_clicked(4)
    txt = m5.headerData(4, Qt.Horizontal, Qt.DisplayRole)
    check("升序箭头是 ▲ 后缀", txt.endswith('▲'), f"got={txt!r}")
    m5.on_header_clicked(4)
    txt2 = m5.headerData(4, Qt.Horizontal, Qt.DisplayRole)
    check("降序箭头是 ▼ 后缀", txt2.endswith('▼'), f"got={txt2!r}")
    check("非活动列无箭头",
          not m5.headerData(1, Qt.Horizontal, Qt.DisplayRole).endswith(('▲', '▼')),
          f"got={m5.headerData(1, Qt.Horizontal, Qt.DisplayRole)!r}")

    section("tooltip: 只在被截断时给完整值")
    m6 = VideoTableModel(VIDEO_COLUMNS)
    m6.append_result(mk('x' * 200 + '.mp4'))
    app.processEvents()
    tip = m6.data(m6.index(0, 1), Qt.ToolTipRole)
    check("截断的列给完整 tooltip", tip == './' + 'x' * 200 + '.mp4',
          f"got={tip!r}")
    tip2 = m6.data(m6.index(0, 2), Qt.ToolTipRole)
    check("没截断的列不给 tooltip", tip2 is None, f"got={tip2!r}")

    section("tooltip — 视图真的向模型要完整值 (真实事件派发)")
    # 原断言 (Tk 版「Tooltip — 真实事件派发」): 光看绑定字符串不够, 得证明
    # 事件真的送到了 handler。Qt 版对应: 派一个真的 ToolTip 事件给视口,
    # 证明视图确实来问模型要 Qt.ToolTipRole 的值。
    from PySide6.QtCore import QEvent, qInstallMessageHandler
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtWidgets import QTableView

    class _SpyModel(VideoTableModel):
        """记下每次有人问 ToolTipRole。"""

        def __init__(self, columns):
            super().__init__(columns)
            self.tooltip_queries = []

        def data(self, index, role=Qt.DisplayRole):
            if role == Qt.ToolTipRole:
                self.tooltip_queries.append((index.row(), index.column()))
            return super().data(index, role)

    spy = _SpyModel(VIDEO_COLUMNS)
    spy.append_result(mk('x' * 200 + '.mp4'))
    spy.append_result(mk('a.mp4'))
    tv = QTableView()
    tv.setModel(spy)
    for i in range(len(VIDEO_COLUMNS)):
        tv.setColumnWidth(i, spy._widths[i])
    tv.resize(1000, 600)
    tv.show()
    app.processEvents()

    idx_cut = spy.index(0, 1)
    pos_cut = tv.visualRect(idx_cut).center()
    spy.tooltip_queries.clear()
    # offscreen 平台没有窗口可 raise → QToolTip 弹 tooltip 时会打一条
    # qWarning ("This plugin does not support raise()")。只在这两条断言
    # 期间吞掉它, 其余 Qt 输出照常。
    def _qt_msg_handler(mode, context, message):
        if 'does not support raise()' not in message:
            sys.stderr.write(message + '\n')

    old_handler = qInstallMessageHandler(_qt_msg_handler)
    try:
        QApplication.sendEvent(tv.viewport(),
                                QHelpEvent(QEvent.ToolTip, pos_cut, pos_cut))
        check("真实 ToolTip 事件 → 视图确实来问模型要 tooltip",
              (0, 1) in spy.tooltip_queries, f"got={spy.tooltip_queries}")
        check("问出来的值是完整值 (不是截断值)",
              spy.data(idx_cut, Qt.ToolTipRole) == './' + 'x' * 200 + '.mp4',
              f"got={spy.data(idx_cut, Qt.ToolTipRole)!r}")

        idx_plain = spy.index(1, 1)
        pos_plain = tv.visualRect(idx_plain).center()
        spy.tooltip_queries.clear()
        QApplication.sendEvent(
            tv.viewport(), QHelpEvent(QEvent.ToolTip, pos_plain, pos_plain))
        check("没截断的格 → 模型不给完整值 (视图没有可弹的内容)",
              spy.data(idx_plain, Qt.ToolTipRole) is None,
              f"got={spy.data(idx_plain, Qt.ToolTipRole)!r}")
    finally:
        qInstallMessageHandler(old_handler)

    section("空表 / 清空")
    m7 = VideoTableModel(VIDEO_COLUMNS)
    check("空表 rowCount=0", m7.rowCount() == 0)
    m7.append_result(mk('a.mp4'))
    check("加一行后 rowCount=1", m7.rowCount() == 1)
    m7.clear()
    check("clear 后 rowCount=0", m7.rowCount() == 0)

    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
