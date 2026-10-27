"""Qt 表格的虚拟滚动 / 数据管理 一次性断言脚本。

Tk 版测的是 VideoGridView 的**池 widget** 机制:
40 个池 widget 复用、spacer 高度恒为 0、window item 的 y 坐标偏移、
滚到底时最后一个 slot 重新绑到 data[-1]、没数据时滚轮/滚动条禁用。

Qt 版**没有这套机制** —— QTableView 原生就是虚拟滚动:
只画可见单元格, 视图里一个「行 widget」都没有 (表头 / 视口 / 滚动条
是仅有的几个固定子窗口)。所以本脚本改测:

- 模型只持一份数据 (rowCount 随追加增长, 模型里零 QWidget);
  **视图的子窗口数不随行数增长** —— 这是「池大小恒定」的等价物,
  也正是「恢复时不用逐个重绘几百个 HWND」的量化形式;
- 追加 → data(index) 立刻是新值; 滚动不改行的内容, 滚到底最后一行可见;
- 列宽变化**立即生效** (Qt 没有节流, 没有 pending flush 这回事);
- clear() → rowCount 归零 / 无可滚内容 / 过期 index 安全;
- reorder() 就地重排, 行对象不重建;
- 模型的公开 API 仍被遵守。

运行: python docs/superpowers/specs/2026-10-14-virtual-scroll-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication, QTableView, QWidget

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


def make_info(
    i: int,
    is_passing: bool = True,
    bitrate_kbps: float = 1000.0,
) -> VideoInfo:
    return VideoInfo(
        rel_path=".",
        title=f"video_{i:04d}",
        resolution="1920x1080",
        frame_rate=f"{30.0 + i * 0.01:.2f}",
        bitrate_kbps=bitrate_kbps,
        video_codec="h264",
        audio_codec="aac",
        audio_channels="2",
        audio_sample_rate="48.0",
        duration="00:01:00",
        file_size="10 MB",
        is_passing=is_passing,
        sample_rate_passing=True,
        bitrate_std=30000.0,
        sample_rate_std=48.0,
        full_path=f"/tmp/video_{i:04d}.mp4",
        container_format="mp4",
        frame_rate_fps=30.0 + i * 0.01,
        sample_rate_khz=48.0,
    )


def child_widget_count(view) -> int:
    """视图子树里的 QWidget 数 —— Qt 版「池大小」的对应量。"""
    return len(view.findChildren(QWidget))


def make_view(model, width: int = 1000, height: int = 600) -> QTableView:
    """建一个按模型列宽设好列宽的 QTableView (app 层将来也这么接线)。"""
    view = QTableView()
    view.setModel(model)
    for i in range(model.columnCount()):
        view.setColumnWidth(i, model._widths[i])
    view.resize(width, height)
    view.show()
    app.processEvents()
    return view


def main() -> int:
    from video_qt import VIDEO_COLUMNS, VideoTableModel

    m = VideoTableModel(VIDEO_COLUMNS)

    section("数据与视图分离 — 模型只持一份数据")
    check("初始 rowCount = 0", m.rowCount() == 0, f"got={m.rowCount()}")
    check("初始没有行", m.sorted_rows == [], f"got={len(m.sorted_rows)}")
    check("模型里一个 QWidget 都没有 (Qt 版没有池 widget)",
          m.findChildren(QWidget) == [],
          f"got={[type(w).__name__ for w in m.findChildren(QWidget)]}")
    check("columnCount == 列规格数", m.columnCount() == len(VIDEO_COLUMNS),
          f"got={m.columnCount()}")

    for i in range(100):
        m.append_result(make_info(i))
    check("100 次 append_result 后 rowCount == 100", m.rowCount() == 100,
          f"got={m.rowCount()}")
    check("100 次 append_result 后数据长度 == 100",
          len(m.sorted_rows) == 100, f"got={len(m.sorted_rows)}")

    section("虚拟滚动 — 视图的子窗口数不随行数增长")
    view = make_view(m)
    n_before = child_widget_count(view)
    check("视图的子 widget 数很小 (只有表头/视口/滚动条/角按钮)",
          n_before <= 32, f"got={n_before}")
    for i in range(2000):
        m.append_result(make_info(1000 + i))
    app.processEvents()
    n_after = child_widget_count(view)
    check("加 2000 行后视图的子 widget 数**不变**",
          n_after == n_before, f"before={n_before} after={n_after}")
    # Tk 版的表格区每一行都是**原生子窗口** (frame + 一堆 label), 映射数
    # 随行数线性增长; Qt 版视图里一个行 widget 都没有 —— 这就是「恢复时
    # 不用逐个重绘几百个 HWND」的量化形式 (替代被删的 restore-black 脚本)。
    check("模型行数确实涨了 (不是没加上)",
          m.rowCount() == 2100, f"got={m.rowCount()}")
    check("行数超出视口 → 纵向滚动条可滚",
          view.verticalScrollBar().maximum() > 0,
          f"max={view.verticalScrollBar().maximum()}")

    section("追加 → data(index) 立刻是新值")
    app.processEvents()
    last = m.index(m.rowCount() - 1, 1)
    check("最后一行的 data() 就是刚追加的那条",
          m.data(last, Qt.DisplayRole) == './video_2999',
          f"got={m.data(last, Qt.DisplayRole)!r}")
    check("中间行的 data() 与插入序一致",
          [m.data(m.index(r, 1), Qt.DisplayRole) for r in range(3)]
          == ['./video_0000', './video_0001', './video_0002'],
          f"got={[m.data(m.index(r, 1), Qt.DisplayRole) for r in range(3)]}")
    inserted = []
    m2 = VideoTableModel(VIDEO_COLUMNS)
    m2.rowsInserted.connect(lambda *a: inserted.append(a))
    m2.append_result(make_info(0))
    check("append_result 走 beginInsertRows/endInsertRows (发 rowsInserted)",
          len(inserted) == 1, f"got={inserted}")

    section("列宽变化 — 立即生效 (Qt 没有节流)")
    m3 = VideoTableModel(VIDEO_COLUMNS)
    long_info = make_info(0)
    long_info.title = 'x' * 200          # 长标题 → 改列宽看得出截断变化
    m3.append_result(long_info)
    app.processEvents()
    hdr_events = []
    m3.headerDataChanged.connect(lambda *a: hdr_events.append(a))
    widths_seen = []
    texts_seen = []
    for w in (400, 300, 200, 150, 120):
        m3.set_column_width(1, w)
        widths_seen.append(m3._widths[1])
        # 立刻读回来 —— Tk 版要等 33ms 节流 + release 才 flush
        texts_seen.append(m3.data(m3.index(0, 1), Qt.DisplayRole))
    check("连续 5 次改列宽, 每次都立刻生效 (无 pending / 无延迟 flush)",
          widths_seen == [400, 300, 200, 150, 120],
          f"got={widths_seen}")
    check("每次改列宽后单元格立刻按新宽度重新截断 (不是等 release)",
          len(set(texts_seen)) == 5, f"got={texts_seen}")
    check("每次改列宽都通知表头重写",
          len(hdr_events) == 5, f"got={len(hdr_events)}")
    m3.set_column_width(1, 5)
    check("列宽被夹到最小 20px", m3._widths[1] == 20, f"got={m3._widths[1]}")
    check("序号列宽不受别的列 resize 影响 (对应 Tk 版 IDX_COL_WIDTH 固定)",
          m3._widths[0] == VIDEO_COLUMNS[0].width,
          f"got={m3._widths[0]}")
    n_after_clamp = len(hdr_events)
    m3.set_column_width(1, 20)
    check("同一宽度重复设置 → 不重复通知 (幂等)",
          len(hdr_events) == n_after_clamp,
          f"before={n_after_clamp} after={len(hdr_events)}")

    section("clear() — 重置数据, 过期 index 安全")
    m4 = VideoTableModel(VIDEO_COLUMNS)
    for i in range(10):
        m4.append_result(make_info(i))
    app.processEvents()
    stale = m4.index(0, 1)
    resets = []
    m4.modelReset.connect(lambda *a: resets.append(a))
    m4.clear()
    app.processEvents()
    check("clear 后 rowCount = 0", m4.rowCount() == 0, f"got={m4.rowCount()}")
    check("clear 走 beginResetModel/endResetModel (发 modelReset)",
          len(resets) == 1, f"got={resets}")
    check("clear 后没有残留行", m4.sorted_rows == [], f"got={len(m4.sorted_rows)}")
    check("clear 之前取的 index 过期 → data() 返回 None (不抛 IndexError)",
          m4.data(stale, Qt.DisplayRole) is None,
          f"got={m4.data(stale, Qt.DisplayRole)!r}")
    check("clear 之前取的 index 的 tooltip 也是 None (不留陈旧完整值)",
          m4.data(stale, Qt.ToolTipRole) is None,
          f"got={m4.data(stale, Qt.ToolTipRole)!r}")
    m4.append_result(make_info(0))
    check("clear 后再追加 → 又是 1 行", m4.rowCount() == 1, f"got={m4.rowCount()}")

    section("reorder() — 就地重排, 行对象不重建")
    m5 = VideoTableModel(VIDEO_COLUMNS)
    for i in range(8):
        # 码率反向, 让排序可见
        m5.append_result(make_info(i, bitrate_kbps=float(1000 * (8 - i))))
    app.processEvents()
    ids_before = [id(x) for x in m5.sorted_rows]
    m5.sort_key = 'bitrate'
    m5.sort_desc = True          # desc: 大码率在前
    m5.reorder()
    app.processEvents()
    ids_after = [id(x) for x in m5.sorted_rows]
    check("reorder 后行对象是同一批实例 (没销毁/重建)",
          ids_after == ids_before,
          f"before={len(ids_before)} after={len(ids_after)}")
    check("reorder 降序: 首行码率最高", m5.sorted_rows[0].bitrate_kbps == 8000.0,
          f"got={m5.sorted_rows[0].bitrate_kbps}")
    check("reorder 降序: 末行码率最低", m5.sorted_rows[-1].bitrate_kbps == 1000.0,
          f"got={m5.sorted_rows[-1].bitrate_kbps}")
    check("reorder 后行数不变", m5.rowCount() == 8, f"got={m5.rowCount()}")

    section("滚动 — 不影响行的内容, 最后一行可见")
    m6 = VideoTableModel(VIDEO_COLUMNS)
    for i in range(500):
        m6.append_result(make_info(i))
    app.processEvents()
    v6 = make_view(m6)
    titles_before = [m6.data(m6.index(r, 1), Qt.DisplayRole) for r in range(500)]
    v6.scrollToBottom()
    app.processEvents()
    titles_after = [m6.data(m6.index(r, 1), Qt.DisplayRole) for r in range(500)]
    check("滚动不改行的内容", titles_before == titles_after,
          "滚动前后 title 列不一致")
    last_idx = m6.index(m6.rowCount() - 1, 1)
    rect = v6.visualRect(last_idx)
    check("滚到底时最后一行落在视口里 (不是画在看不见的地方)",
          rect.isValid() and rect.intersects(v6.viewport().rect()),
          f"rect={rect} viewport={v6.viewport().rect()}")
    hit = v6.indexAt(QPoint(5, v6.viewport().height() - 5))
    check("滚到底时视口底部命中的是最后一行",
          hit.isValid() and hit.row() == m6.rowCount() - 1,
          f"got row={hit.row() if hit.isValid() else None} "
          f"want={m6.rowCount() - 1}")

    section("行数装得下时没有可滚范围 (对应 Tk 版滚轮/滚动条禁用)")
    m7 = VideoTableModel(VIDEO_COLUMNS)
    for i in range(8):
        m7.append_result(make_info(i))
    app.processEvents()
    v7 = make_view(m7, height=2000)     # 视口足够高, 8 行装得下
    check("8 行装得下 → 纵向滚动条没有可滚范围",
          v7.verticalScrollBar().maximum() == 0,
          f"max={v7.verticalScrollBar().maximum()}")
    check("8 行装得下 → 视口高度确实是 8 行以上",
          v7.viewport().height() > 8 * 20,
          f"h={v7.viewport().height()}")

    section("公开 API — preserved")
    for name in ('rowCount', 'columnCount', 'data', 'headerData',
                 'append_result', 'clear', 'reorder', 'is_sorted',
                 'set_column_width', 'on_header_clicked'):
        check(f"公开方法 {name} 存在",
              hasattr(m, name) and callable(getattr(m, name)))
    m8 = VideoTableModel(VIDEO_COLUMNS)
    check("is_sorted() 初始为 False", not m8.is_sorted())
    m8.append_result(make_info(0))
    m8.on_header_clicked(4)
    check("is_sorted() 排序后为 True", m8.is_sorted())

    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
