"""主窗口表头对齐 + 横向滚动同步 — 一次性断言脚本。

两个既有 bug (2026-10-26 修):

1. 表头与正文错位: 表头原本是正文 canvas 的**兄弟 widget**, 比 canvas 宽
   15px (竖直滚动条占的)。两边 grid 分配到的额外空间不同 → 列宽逐列漂移,
   实测最右列差 14px, 窗口越宽差得越多 (2560px 屏上 ~26px)。

2. 横向滚动时表头不动: 拖宽任一列让内容超出窗口后, 正文滚走了 225px,
   表头还在原地 → 表头标注的列名完全指错对象。

修法: 表头独立成一个 canvas (header_canvas), 与正文 canvas **同宽**
(靠 _build 里的 pack 顺序保证: vsb 先占右侧 15px, 表头与正文 canvas
拿到同样宽的剩余空间), scrollregion 宽度也相同 → 两边 grid 分配一致 → Δ=0;
横向滚动时通过 xscrollcommand 同步表头的 xview。

跑之前是坏的: Δ 最大 14px; 横滚后表头不动, Δ = -225px。

运行: python docs/superpowers/specs/2026-10-26-header-alignment-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

from video_checker import VideoInfo  # noqa: E402


_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "✓" if condition else "✗"
    line = f"  {mark} {name}" + (f" — {detail}" if detail else "")
    print(line)
    if not condition:
        _failures.append(line)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def make_info(title='a.mp4', is_passing=True) -> VideoInfo:
    return VideoInfo(
        rel_path='./', title=title, resolution='1920x1080', frame_rate='25.00',
        bitrate_kbps=32000.0, video_codec='h264', audio_codec='aac',
        audio_channels='2', audio_sample_rate='48.0', duration='00:01:00',
        file_size='1.0 GB', is_passing=is_passing, sample_rate_passing=True,
        bitrate_std=30000.0, sample_rate_std=48.0,
        full_path=f'/x/{title}', container_format='mp4',
    )


class FakeEvent:
    """给 _start_col_resize / _on_col_resize 用的最小事件对象。"""

    def __init__(self, x_root: int):
        self.x_root = x_root
        self.x = 0
        self.y = 0


def main() -> int:
    from tkinterdnd2 import TkinterDnD
    from video_checker import VideoCheckerApp
    # 必须用 TkinterDnD.Tk(): VideoGridView.bind_drop 会调 drop_target_register,
    # 普通 tk.Tk() 上会 AttributeError。
    root = TkinterDnD.Tk()
    root.geometry('1280x700')
    app = VideoCheckerApp(root)
    root.update_idletasks()
    for i in range(60):
        app._add_result(make_info(f'v{i}.mp4', is_passing=(i % 3 == 0)))
    root.update_idletasks()
    root.update()
    grid = app.grid
    keys = list(grid.columns.keys())

    section("表头与正文 canvas 同宽")
    check("表头 canvas 存在", hasattr(grid, 'header_canvas'))
    check("表头 canvas 宽 = 正文 canvas 宽",
          grid.header_canvas.winfo_width() == grid.canvas.winfo_width(),
          f"header={grid.header_canvas.winfo_width()} "
          f"body={grid.canvas.winfo_width()}")
    check("表头没有被裁切 (canvas 高 = frame 高)",
          grid.header_canvas.winfo_height() == grid.header_frame.winfo_height(),
          f"canvas={grid.header_canvas.winfo_height()} "
          f"frame={grid.header_frame.winfo_height()}")
    for key, lbl in list(grid._header_labels.items())[:3]:
        bottom = (lbl.winfo_rooty() - grid.header_canvas.winfo_rooty()
                  + lbl.winfo_height())
        check(f"表头 label「{key}」完整显示",
              bottom <= grid.header_canvas.winfo_height(),
              f"bottom={bottom} canvas_h={grid.header_canvas.winfo_height()}")

    section("表头与正文逐列对齐 (Δ=0)")
    deltas = []
    for i, key in enumerate(keys):
        hx = grid._header_labels[key].winfo_rootx()
        bx = grid._widget_pool[0]._cells[i][1].winfo_rootx()
        deltas.append((key, bx - hx))
    worst = max(abs(d) for _k, d in deltas)
    for key, d in deltas:
        check(f"「{key}」Δ=0", d == 0, f"Δ={d}")
    check("最大错位 = 0px (改之前是 14px)", worst == 0, f"got={worst}")

    section("横向滚动时表头跟着动")
    grid._col_widths['title'] = 600
    grid.header_frame.grid_columnconfigure(1, minsize=600)
    for w in grid._widget_pool:
        w.grid_columnconfigure(1, minsize=600)
    grid._render_visible(force=True)
    root.update_idletasks()
    root.update()
    h0 = grid._header_labels['title'].winfo_rootx()
    b0 = grid._widget_pool[0]._cells[0][1].winfo_rootx()
    grid.canvas.xview_moveto(1.0)
    root.update_idletasks()
    root.update()
    h1 = grid._header_labels['title'].winfo_rootx()
    b1 = grid._widget_pool[0]._cells[0][1].winfo_rootx()
    check("正文确实横向滚动了", b1 != b0, f"{b0}→{b1}")
    check("表头也跟着滚了同样的距离", (h1 - h0) == (b1 - b0),
          f"表头动 {h1-h0}px, 正文动 {b1-b0}px")
    check("横滚后表头与正文仍 Δ=0", (b1 - h1) == 0, f"Δ={b1-h1}")

    section("拖宽列后表头列宽同步")
    grid._col_widths['title'] = grid.columns['title'][1]
    grid.header_frame.grid_columnconfigure(1, minsize=grid.columns['title'][1])
    for w in grid._widget_pool:
        w.grid_columnconfigure(1, minsize=grid.columns['title'][1])
    grid._render_visible(force=True)
    root.update_idletasks()
    root.update()
    # 走 app 自己的列宽调整路径
    grid._start_col_resize(FakeEvent(1000), 'title')
    grid._on_col_resize(FakeEvent(1000 + 120), 'title')
    root.update_idletasks()
    root.update()
    check("列宽 dict 更新了", grid._col_widths['title'] == grid.columns['title'][1],
          f"got={grid._col_widths['title']}")
    deltas2 = []
    for i, key in enumerate(keys):
        hx = grid._header_labels[key].winfo_rootx()
        bx = grid._widget_pool[0]._cells[i][1].winfo_rootx()
        deltas2.append(bx - hx)
    check("拖宽后仍然逐列 Δ=0", all(d == 0 for d in deltas2),
          f"deltas={deltas2}")

    section("表头点击排序仍可用")
    grid._sort_key = None
    grid._sort_desc = False
    grid._header_labels['bitrate'].event_generate('<Button-1>')
    root.update_idletasks()
    check("点了表头后进入排序态", grid._sort_key == 'bitrate',
          f"got={grid._sort_key!r}")

    root.destroy()
    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
