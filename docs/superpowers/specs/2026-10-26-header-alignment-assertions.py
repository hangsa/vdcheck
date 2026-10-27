"""Qt 表格的表头/正文对齐 + 横向滚动同步 — 一次性断言脚本。

Tk 版测的是两个既有 bug (2026-10-26 修):

1. 表头与正文错位: 表头原本是正文 canvas 的**兄弟 widget**, 比 canvas 宽
   15px (竖直滚动条占的)。两边 grid 分配到的额外空间不同 → 列宽逐列漂移,
   实测最右列差 14px, 窗口越宽差得越多 (2560px 屏上 ~26px)。

2. 横向滚动时表头不动: 拖宽任一列让内容超出窗口后, 正文滚走了 225px,
   表头还在原地 → 表头标注的列名完全指错对象。

Qt 版里表头与正文在**同一个 QTableView** 里 —— 表头是 view 的
horizontalHeader(), 列宽只有一份 (模型的 `_widths`), 表头 section 与
正文列由同一组 section size 布局。所以对齐是结构性的, 但照样要断言:

- 表头与正文视口同宽 (表头没有被另一个 canvas 挤掉 15px)
- 逐列 Δ=0: 表头 section 的 x == 正文列的 x
- 横向滚动时表头跟着正文动同样的距离, 滚完仍 Δ=0
- 改列宽后表头列宽与正文同步, 表头文字按新宽度重新截断
- 排序后列位置不变; 表头点击排序仍可用

运行: python docs/superpowers/specs/2026-10-26-header-alignment-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication, QTableView

# QApplication 必须先于 video_qt 建好: video_qt 的 UI_FONT = QFont() 会拷贝
# **当前 app 的默认字体**, app 还不存在时字体族留空 → 字宽度量跟真机对不上。
app = QApplication.instance() or QApplication(sys.argv)

from video_core import VideoInfo

_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "✓" if condition else "✗"
    line = f"  {mark} {name}" + (f" — {detail}" if detail else "")
    print(line)
    if not condition:
        _failures.append(line)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def make_info(title='a.mp4', is_passing=True, bitrate=32000.0) -> VideoInfo:
    return VideoInfo(
        rel_path='./', title=title, resolution='1920x1080', frame_rate='25.00',
        bitrate_kbps=bitrate, video_codec='h264', audio_codec='aac',
        audio_channels='2', audio_sample_rate='48.0', duration='00:01:00',
        file_size='1.0 GB', is_passing=is_passing, sample_rate_passing=True,
        bitrate_std=30000.0, sample_rate_std=48.0,
        full_path=f'/x/{title}', container_format='mp4',
        frame_rate_fps=25.0, sample_rate_khz=48.0,
    )


def build(columns):
    """建模型 + 视图, 并按模型的列宽设好视图的 section size (app 层就这么接线)。"""
    from video_qt import VideoTableModel
    model = VideoTableModel(columns)
    view = QTableView()
    view.setModel(model)
    for i in range(model.columnCount()):
        view.setColumnWidth(i, model._widths[i])
    view.resize(1280, 700)
    view.show()
    app.processEvents()
    return model, view


def main() -> int:
    from video_qt import VIDEO_COLUMNS, UI_FONT

    model, view = build(VIDEO_COLUMNS)
    for i in range(60):
        model.append_result(make_info(f'v{i}.mp4', is_passing=(i % 3 == 0),
                                      bitrate=60000.0 - i * 500))
    app.processEvents()
    hdr = view.horizontalHeader()
    ncols = model.columnCount()

    section("表头与正文同宽 (同一个 QTableView)")
    check("表头就是 view 的 horizontalHeader (不是另一个 canvas)",
          hdr is not None and hdr.parent() is view,
          f"parent={hdr.parent() if hdr else None}")
    check("表头宽 = 正文视口宽 (Tk 版差 15px 就是竖直滚动条占的)",
          hdr.width() == view.viewport().width(),
          f"header={hdr.width()} viewport={view.viewport().width()}")
    check("表头没有被裁切 (高度 = sizeHint)",
          hdr.height() == hdr.sizeHint().height(),
          f"h={hdr.height()} sizeHint={hdr.sizeHint().height()}")
    fm = QFontMetrics(UI_FONT)
    for i, col in enumerate(VIDEO_COLUMNS):
        txt = model.headerData(i, Qt.Horizontal, Qt.DisplayRole)
        check(f"表头「{col.header}」装得进列宽 (没被切掉)",
              fm.horizontalAdvance(txt) <= hdr.sectionSize(i),
              f"text_w={fm.horizontalAdvance(txt)} section={hdr.sectionSize(i)}")

    section("表头与正文逐列对齐 (Δ=0)")
    deltas = []
    for i in range(ncols):
        d = hdr.sectionViewportPosition(i) - view.columnViewportPosition(i)
        deltas.append(d)
        check(f"第 {i} 列「{VIDEO_COLUMNS[i].header}」Δ=0", d == 0, f"Δ={d}")
    check("最大错位 = 0px (Tk 版改之前是 14px)",
          max(abs(d) for d in deltas) == 0,
          f"got={max(abs(d) for d in deltas)}")
    check("表头 section 宽度 == 模型的列宽 (列宽只有一个来源)",
          all(hdr.sectionSize(i) == model._widths[i] for i in range(ncols)),
          f"got={[(hdr.sectionSize(i), model._widths[i]) for i in range(ncols)]}")

    section("横向滚动时表头跟着动")
    # 拖宽标题列, 让内容宽出视口 → 才有横向滚动可言
    model.set_column_width(1, 600)
    view.setColumnWidth(1, 600)
    app.processEvents()
    check("拖宽后有横向可滚范围",
          view.horizontalScrollBar().maximum() > 0,
          f"max={view.horizontalScrollBar().maximum()}")
    h0 = hdr.sectionViewportPosition(1)
    b0 = view.columnViewportPosition(1)
    view.horizontalScrollBar().setValue(view.horizontalScrollBar().maximum())
    app.processEvents()
    h1 = hdr.sectionViewportPosition(1)
    b1 = view.columnViewportPosition(1)
    check("正文确实横向滚动了", b1 != b0, f"{b0}→{b1}")
    check("表头也跟着滚了同样的距离", (h1 - h0) == (b1 - b0),
          f"表头动 {h1 - h0}px, 正文动 {b1 - b0}px")
    check("横滚后表头与正文仍 Δ=0", (b1 - h1) == 0, f"Δ={b1 - h1}")
    deltas2 = [hdr.sectionViewportPosition(i) - view.columnViewportPosition(i)
               for i in range(ncols)]
    check("横滚后逐列仍全部 Δ=0", all(d == 0 for d in deltas2),
          f"deltas={deltas2}")

    section("拖宽列后表头列宽同步")
    model.set_column_width(4, 300)          # 码率列: 表头文字最长的一列
    view.setColumnWidth(4, 300)
    app.processEvents()
    check("表头 section 宽度跟上了",
          hdr.sectionSize(4) == 300, f"got={hdr.sectionSize(4)}")
    check("表头列宽 == 模型的列宽",
          hdr.sectionSize(4) == model._widths[4],
          f"section={hdr.sectionSize(4)} model={model._widths[4]}")
    wide_text = model.headerData(4, Qt.Horizontal, Qt.DisplayRole)
    check("拉宽后表头文字是完整文字 (没被截断)",
          wide_text == '码率(kbps)', f"got={wide_text!r}")
    check("拉宽后表头文字装得进新列宽",
          fm.horizontalAdvance(wide_text) <= hdr.sectionSize(4),
          f"w={fm.horizontalAdvance(wide_text)} section={hdr.sectionSize(4)}")
    # 收窄 → 表头文字按新宽度重新截断 (与现状 _refresh_headers 同序)
    model.set_column_width(4, 40)
    view.setColumnWidth(4, 40)
    app.processEvents()
    narrow_text = model.headerData(4, Qt.Horizontal, Qt.DisplayRole)
    check("收窄后表头文字被重新截断",
          narrow_text.endswith('...') and narrow_text != wide_text,
          f"got={narrow_text!r}")
    check("收窄后表头文字装得进新列宽",
          fm.horizontalAdvance(narrow_text) <= hdr.sectionSize(4),
          f"w={fm.horizontalAdvance(narrow_text)} section={hdr.sectionSize(4)}")
    deltas3 = [hdr.sectionViewportPosition(i) - view.columnViewportPosition(i)
               for i in range(ncols)]
    check("改列宽后仍然逐列 Δ=0", all(d == 0 for d in deltas3),
          f"deltas={deltas3}")

    section("排序后列位置不变")
    before_pos = [hdr.sectionViewportPosition(i) for i in range(ncols)]
    before_sizes = [hdr.sectionSize(i) for i in range(ncols)]
    model.on_header_clicked(4)          # 码率列
    app.processEvents()
    after_pos = [hdr.sectionViewportPosition(i) for i in range(ncols)]
    after_sizes = [hdr.sectionSize(i) for i in range(ncols)]
    check("排序后列的 x 位置没变", before_pos == after_pos,
          f"before={before_pos} after={after_pos}")
    check("排序后列宽没变", before_sizes == after_sizes,
          f"before={before_sizes} after={after_sizes}")

    section("表头点击排序仍可用")
    model.sort_key = None
    model.sort_desc = False
    model.on_header_clicked(4)
    check("点了码率表头后进入排序态", model.sort_key == 'bitrate',
          f"got={model.sort_key!r}")
    check("排序后行顺序真的变了",
          [model.data(model.index(r, 1), Qt.DisplayRole) for r in range(3)]
          == ['./v59.mp4', './v58.mp4', './v57.mp4'],
          f"got={[model.data(model.index(r, 1), Qt.DisplayRole) for r in range(3)]}")
    # 走真实的「点表头」链路: view 的 sectionClicked → 模型
    model2, view2 = build(VIDEO_COLUMNS)
    for i in range(5):
        model2.append_result(make_info(f'w{i}.mp4', bitrate=1000.0 * (5 - i)))
    app.processEvents()
    view2.horizontalHeader().sectionClicked.connect(model2.on_header_clicked)
    view2.horizontalHeader().sectionClicked.emit(4)
    app.processEvents()
    check("表头 sectionClicked → 模型进入排序态",
          model2.sort_key == 'bitrate', f"got={model2.sort_key!r}")
    check("表头 sectionClicked → 行顺序真的变了",
          [model2.data(model2.index(r, 1), Qt.DisplayRole) for r in range(5)]
          == ['./w4.mp4', './w3.mp4', './w2.mp4', './w1.mp4', './w0.mp4'],
          f"got={[model2.data(model2.index(r, 1), Qt.DisplayRole) for r in range(5)]}")

    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
