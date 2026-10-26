"""最小化恢复后表格区露底 (黑屏) — 一次性断言脚本。

背景:
    表格区不是画在 canvas 上的, 是几百个**原生子窗口**:
    27 行 × (1 个 ttk.Frame + 11 个 tk.Label) ≈ 328 个。
    Windows 上每个都是独立 HWND, 窗口从最小化恢复时要逐个重新绘制,
    画完之前那片区域没人画 → 用户看到的是一片黑。

    窗口里映射着的子窗口越多, 黑得越久 (用户实测: 3 个文件 vs 60 个文件,
    后者明显更久)。表格空的时候也不该留着子窗口 —— 「清除记录」以前
    只清文本、不把池 widget 移出布局, 于是扫过 60 个文件再清除记录,
    canvas 里仍映射着 297 个, 恢复时照样逐个重绘。

本脚本验证「没在用的行不映射」:
- 从没扫描过的 app: 池 widget 一个都不映射 (以前是 40 行全映射 = 484 个)
- clear() 之后: 映射行数 = 0
- _render_visible(n=0) 之后: 映射行数 = 0
- 行高测量不受影响 (未映射的 widget 的 winfo_reqheight 照样有效)
- 清空后再加数据: 行被重新 pack, 顺序正确

跑之前是坏的 (2026-10-26 修):
    - 池 widget 建好就 pack, 从没扫描过的 app 也有 440 个标签映射着
    - clear() / _render_visible(n=0) 都没 pack_forget

运行: python docs/superpowers/specs/2026-10-26-restore-black-assertions.py
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


def mapped_widgets(widget) -> int:
    """widget 子树里**映射着**的 widget 数 (含自身)。"""
    n = 1 if widget.winfo_ismapped() else 0
    for c in widget.winfo_children():
        n += mapped_widgets(c)
    return n


def mapped_rows(grid) -> int:
    return sum(1 for w in grid._widget_pool if w.winfo_manager())


def main() -> int:
    from tkinterdnd2 import TkinterDnD
    from video_checker import VideoCheckerApp
    # 必须用 TkinterDnD.Tk(): VideoGridView.bind_drop 会调 drop_target_register,
    # 普通 tk.Tk() 上会 AttributeError。
    root = TkinterDnD.Tk()
    root.geometry('1280x700')
    app = VideoCheckerApp(root)
    root.update_idletasks()
    grid = app.grid

    section("从没扫描过: 池 widget 一个都不该映射")
    check("映射行数 = 0", mapped_rows(grid) == 0,
          f"got={mapped_rows(grid)}")
    # canvas + data_frame + 两个 spacer = 4; 池 widget 一个都不该在里面
    check("canvas 内映射 widget ≤ 10 (以前是 484)",
          mapped_widgets(grid.canvas) <= 10,
          f"got={mapped_widgets(grid.canvas)}")
    check("池 widget 一个都没被销毁 (只是没映射)",
          len(grid._widget_pool) == grid.POOL_SIZE,
          f"got={len(grid._widget_pool)} want={grid.POOL_SIZE}")

    section("行高测量不受未映射影响")
    check("行高已测出 (不是兜底值 26)", grid._row_height not in (0, 26),
          f"got={grid._row_height}")
    check("行高 = 池里第一行的 reqheight",
          grid._row_height == grid._widget_pool[0].winfo_reqheight(),
          f"got={grid._row_height} "
          f"reqheight={grid._widget_pool[0].winfo_reqheight()}")

    section("有数据时: 只映射用到的行")
    for i in range(60):
        app._add_result(make_info(f'v{i}.mp4', is_passing=(i % 3 == 0)))
    root.update_idletasks()
    root.update()
    n_mapped = mapped_rows(grid)
    check("映射行数 > 0 (数据在显示)", n_mapped > 0, f"got={n_mapped}")
    check("映射行数 ≤ 池大小", n_mapped <= len(grid._widget_pool),
          f"got={n_mapped}")
    check("第一行显示的是第一条数据",
          grid._widget_pool[0]._cells[0][1].cget('text') == './v0.mp4',
          f"got={grid._widget_pool[0]._cells[0][1].cget('text')!r}")
    check("映射的行里没有空文本的尾行",
          all(grid._widget_pool[i]._cells[0][1].cget('text') != ''
              for i in range(n_mapped)),
          f"got={n_mapped} 行")

    section("清除记录: 池 widget 全部移出布局")
    grid.clear()
    root.update_idletasks()
    root.update()
    check("映射行数 = 0", mapped_rows(grid) == 0, f"got={mapped_rows(grid)}")
    check("canvas 内映射 widget ≤ 10 (以前清除后仍 328)",
          mapped_widgets(grid.canvas) <= 10,
          f"got={mapped_widgets(grid.canvas)}")

    section("_render_visible(n=0) 也要移出布局")
    for i in range(30):
        app._add_result(make_info(f'x{i}.mp4'))
    root.update_idletasks()
    root.update()
    check("先有数据时映射行数 > 0", mapped_rows(grid) > 0,
          f"got={mapped_rows(grid)}")
    grid._data.clear()
    grid._render_visible()
    root.update_idletasks()
    check("n=0 后映射行数 = 0", mapped_rows(grid) == 0,
          f"got={mapped_rows(grid)}")

    section("清空后再加数据: 行要重新 pack 且顺序正确")
    for i in range(5):
        app._add_result(make_info(f'w{i}.mp4'))
    root.update_idletasks()
    root.update()
    check("映射行数 = 5", mapped_rows(grid) == 5, f"got={mapped_rows(grid)}")
    check("顺序是 w0..w4 (重新 pack 时不能乱序)",
          [grid._widget_pool[i]._cells[0][1].cget('text')
           for i in range(5)] == ['./w0.mp4', './w1.mp4', './w2.mp4',
                                  './w3.mp4', './w4.mp4'],
          f"got={[grid._widget_pool[i]._cells[0][1].cget('text') for i in range(5)]!r}")

    root.destroy()
    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
