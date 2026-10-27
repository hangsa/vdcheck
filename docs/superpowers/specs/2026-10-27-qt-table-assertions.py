"""Qt 表格模型的行为断言。

对照 docs/产品文档.md 的表格规格 (§4.2) 与着色规则 (§3.3)。
跑法: python docs/superpowers/specs/2026-10-27-qt-table-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

from PySide6.QtCore import Qt, QModelIndex
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication

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
    from video_core import VideoInfo, _cell_fg

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
    # 截断到几行取决于字体度量 (CJK 字宽各平台不同), 只断言「被重新截断了」
    check("变窄后表头被重新截断",
          after_hdr != before_hdr and after_hdr.endswith('...'),
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
