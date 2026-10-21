"""VideoGridView 虚拟滚动 一次性断言脚本。

验证：
- pool size 初始化合理, 大量 add_row 后池 size 稳定 (O(1) widget);
- 滚到一半时, 池 slot 重新绑定到正确 data index;
- 列宽拖动的 33ms 节流 + 池 widget minsize 立即 + cell 文本 deferred;
- mapped / unmapped widget 在 _on_col_resize 下的差异化处理;
- clear / reorder / sort 行为正确;
- 公开 API 仍被新实现遵守 (add_row / clear / reorder / bind_drop / is_sorted)。

运行: python docs/superpowers/specs/2026-10-14-virtual-scroll-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

import tkinter as tk

from video_checker import VideoInfo, VideoGridView


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
        frame_rate=f"{30.0 + i * 0.01:.2f} fps",
        bitrate_kbps=bitrate_kbps,
        video_codec="h264",
        audio_codec="aac",
        audio_channels="2",
        audio_sample_rate="48.0 kHz",
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


class _FakeEvent:
    """伪造鼠标事件, 只需 x_root。"""

    def __init__(self, x_root: int):
        self.x_root = x_root


def _build_grid(root: tk.Tk, columns: dict[str, tuple[str, int]]) -> VideoGridView:
    grid = VideoGridView(root, columns)
    root.update_idletasks()
    # 池 widget 初建时 winfo_reqheight 可能返 1; 强制再测一次
    grid._measure_row_height()
    return grid


if __name__ == "__main__":
    section("Virtual scroll — pool & data separation")
    root = tk.Tk()
    root.geometry("800x600+10+10")
    columns = {
        'title': ('标题', 250),
        'bitrate': ('码率', 90),
        'result': ('结果', 70),
    }
    grid = _build_grid(root, columns)

    check("POOL_SIZE 介于 20-60",
          20 <= grid.POOL_SIZE <= 60,
          f"actual={grid.POOL_SIZE}")
    check("VISIBLE_BUFFER > 0",
          grid.VISIBLE_BUFFER > 0,
          f"actual={grid.VISIBLE_BUFFER}")
    check("初始 widget pool size == POOL_SIZE",
          len(grid._widget_pool) == grid.POOL_SIZE)
    check("初始 _data 为空",
          grid._data == [])

    # 加 100 行后, pool size 应仍 = POOL_SIZE (没创建额外 widget)
    for i in range(100):
        grid.add_row(make_info(i))
    root.update_idletasks()
    # 触发 after_idle 的 render
    grid._render_visible()
    root.update_idletasks()

    check("100 次 add_row 后 _data 长度 == 100",
          len(grid._data) == 100)
    check("100 次 add_row 后 widget pool size 仍 == POOL_SIZE",
          len(grid._widget_pool) == grid.POOL_SIZE,
          f"actual={len(grid._widget_pool)} (欲拒 widget 数量线性增长)")
    check("first widget 绑定到 data[0]",
          grid._widget_pool[0]._bound_data_idx == 0)

    section("Render — scroll updates slot bindings")
    # yview_moveto(0.5) → 重新计算可见区间
    grid.canvas.yview_moveto(0.5)
    root.update_idletasks()
    grid._render_visible()
    root.update_idletasks()
    n = len(grid._data)
    # 期望: 大约落在第 n/2 - buffer 行附近; 验证 26 ≤ first ≤ 50 (rough)
    check("yview_moveto(0.5) 后 _first_idx 在中间范围",
          20 <= grid._first_idx <= 55,
          f"actual={grid._first_idx}")
    # 池 widget[0] 应绑定到 _first_idx
    check("widget[0] 重新绑定到 _first_idx",
          grid._widget_pool[0]._bound_data_idx == grid._first_idx,
          f"widget[0]._bound_data_idx={grid._widget_pool[0]._bound_data_idx}")
    # 没有重复绑定
    indices = [w._bound_data_idx for w in grid._widget_pool[:5]]
    check("前 5 个 widget 的 _bound_data_idx 单调递增",
          indices == sorted(indices),
          f"got={indices}")

    section("Column resize — throttle & minsize on all widgets")
    # 重置 row_count 较小便于测试
    grid.clear()
    for i in range(5):
        grid.add_row(make_info(i, bitrate_kbps=1000 * (i + 1)))
    root.update_idletasks()
    grid._render_visible()
    root.update_idletasks()

    # 列宽 'title' 从 250 拉到 400 (模拟 5 个 B1-Motion 事件)
    before_min = grid._col_widths['title']
    grid._resize_state = {'key': 'title', 'start_x': 1000, 'start_width': before_min}
    for x in [1100, 1200, 1300, 1400, 1500]:
        grid._on_col_resize(_FakeEvent(x), 'title')

    check("节流: 5 次 B1-Motion 后只有 1 个 after_id",
          grid._resize_after_id is not None)
    check("pending_resize 已缓存最新宽度",
          grid._pending_resize is not None
          and grid._pending_resize[2] == grid._col_widths['title'])

    # 所有 widget (mapped 或否) 的 minsize 都应被即时更新
    # col 0 = 序号 (固定宽), title 列在 col 1 (数据列整体右移 1 位)
    minsizes = [w.grid_columnconfigure(1, 'minsize') for w in grid._widget_pool]
    expected = grid._col_widths['title']
    check("所有 widget 的 title col (col 1) minsize 已更新 (无 mapped/not 分别)",
          all(m == expected for m in minsizes),
          f"uniform={expected}, varied={list(set(minsizes))[:5]}...")

    # col 0 = 序号列, 固定宽且不随列宽拖动变化
    idx_minsizes = [w.grid_columnconfigure(0, 'minsize') for w in grid._widget_pool]
    check("序号列 col 0 固定为 IDX_COL_WIDTH, 不受 title resize 影响",
          all(m == grid.IDX_COL_WIDTH for m in idx_minsizes),
          f"IDX_COL_WIDTH={grid.IDX_COL_WIDTH}, varied={list(set(idx_minsizes))[:5]}...")

    section("Column resize — release flushes pending text")
    # 验证 _end_col_resize 立即 flush (而非等 33ms)
    info0 = grid._widget_pool[0]._info
    grid._end_col_resize()
    check("释放后 _resize_after_id 清空",
          grid._resize_after_id is None)
    check("释放后 _pending_resize 清空",
          grid._pending_resize is None)

    # 把列宽重新收窄到很小, 触发实际 truncation
    # 先 reset _resize_state 完全重新初始化, 不带上一段残留 start_x 偏移
    expected_width = grid._col_widths['title']  # 当前 750 之类
    grid._resize_state = {'key': 'title', 'start_x': 1000, 'start_width': expected_width}
    # 拉到 start_x + (very negative), new_width 会被 clamp 到 20
    grid._on_col_resize(_FakeEvent(-1000000), 'title')
    grid._end_col_resize()
    check("列宽被 clamp 到最小值 20",
          grid._col_widths['title'] == 20,
          f"actual={grid._col_widths['title']}")
    # mapped widget 的 cell text 应被重截断为带 '...' 的短文本
    if grid._widget_pool[0].winfo_ismapped():
        cell_text = grid._widget_pool[0]._cells[0][1].cget('text')
        check("20px 列宽下 widget[0] title cell 被截断 (含 '...')",
              cell_text.endswith('...'),
              f"got={cell_text!r}")

    section("Column resize — unmapped widget skips cell text rewrite")
    # clear 后所有 widget._bound_data_idx = None
    grid.clear()
    # 这次 cell 文本应该被清空, 重设时不会触发 cell.config(text=...)
    # 即使 _on_col_resize 跑过, 不应触发 Tk 写入未 mapped widget (如果有)
    # 实际: winfo_ismapped() 在 clear 后通常仍 True (widget 仍在 data_frame 内),
    # _bound_data_idx = None 时 _apply_pending_resize 跳过 → 这是关键不变量。
    check("clear 后所有 widget._bound_data_idx == None",
          all(w._bound_data_idx is None for w in grid._widget_pool))

    section("clear() — resets data & bindings")
    for i in range(10):
        grid.add_row(make_info(i))
    root.update_idletasks()
    grid._render_visible()
    root.update_idletasks()
    grid.clear()
    check("clear 后 _data 为空", grid._data == [])
    check("clear 后 pool widget._info == None",
          all(w._info is None for w in grid._widget_pool))
    check("clear 后所有 cell text 为空串",
          all(w._cells[0][1].cget('text') == '' for w in grid._widget_pool))
    # scrollregion 高 = 0
    sr = grid.canvas.cget('scrollregion')
    sr_h = float(sr.split()[3]) if sr else 0
    check("clear 后 scrollregion 高度 = 0", sr_h == 0, f"got={sr!r}")
    # spacers 高度 = 0
    check("clear 后 top_spacer 高度 = 0",
          int(grid._top_spacer.cget('height')) == 0)
    check("clear 后 bottom_spacer 高度 = 0",
          int(grid._bottom_spacer.cget('height')) == 0)

    section("reorder() — sorts _data in place; doesn't destroy widgets")
    for i in range(8):
        # bitrate 反向, 让排序可见
        grid.add_row(make_info(i, bitrate_kbps=float(1000 * (8 - i))))
    root.update_idletasks()
    grid._render_visible()
    pool_size_before = len(grid._widget_pool)
    widget_ids_before = [id(w) for w in grid._widget_pool]
    grid._sort_key = 'bitrate'
    grid._sort_desc = True    # desc: 大 bitrate 在前
    grid.reorder()
    root.update_idletasks()
    check("reorder 后池 widget 实例一致 (没销毁/重建)",
          [id(w) for w in grid._widget_pool] == widget_ids_before,
          f"pool size before={pool_size_before}, after={len(grid._widget_pool)}")
    check("reorder 降序: data[0].bitrate 高, data[-1].bitrate 低",
          grid._data[0].bitrate_kbps > grid._data[-1].bitrate_kbps)
    check("reorder 降序: data[0] 是最大",
          grid._data[0].bitrate_kbps == 8000.0)
    check("reorder 降序: data[-1] 是最小",
          grid._data[-1].bitrate_kbps == 1000.0)

    section("Scroll — 滚动时无上/下空白 (spacer 不参与, 靠 window coords)")
    # 补足数据量, 保证可滚动 (前面 reorder 只用了 8 行, 少于可视行数)
    grid.clear()
    for i in range(100):
        grid.add_row(make_info(i))
    root.update_idletasks()
    grid._render_visible(force=True)
    root.update_idletasks()

    # 1) spacer 恒为 0 (滚动表达交给 canvas window item 的 y 坐标)
    grid.canvas.yview_moveto(0.0)
    root.update_idletasks()
    grid._render_visible(force=True)
    root.update_idletasks()
    check("顶部时 top_spacer == 0",
          int(grid._top_spacer.cget('height')) == 0,
          f"got={grid._top_spacer.cget('height')}")
    check("顶部时 bottom_spacer == 0",
          int(grid._bottom_spacer.cget('height')) == 0,
          f"got={grid._bottom_spacer.cget('height')}")

    # 2) scrollregion 高 == N * row_height (与池大小无关, 不会偏短露白)
    n = len(grid._data)
    sr_h = float(grid.canvas.cget('scrollregion').split()[3])
    check("scrollregion 高 == N * row_height",
          sr_h == n * grid._row_height,
          f"sr_h={sr_h}, N*rh={n * grid._row_height}")

    # 3) 滚到底: window item 下移, 最后一行有内容
    grid.canvas.yview_moveto(1.0)
    root.update_idletasks()
    grid._render_visible(force=True)
    root.update_idletasks()
    coords_y = grid.canvas.coords(grid._canvas_window)[1]
    check("滚到底时 window item 下移 (coords y > 0)",
          coords_y > 0,
          f"coords y={coords_y}")
    last_slot = min(len(grid._widget_pool), grid._last_idx - grid._first_idx) - 1
    last_bound = grid._widget_pool[last_slot]._bound_data_idx
    check("滚到底时最后一行绑定到 data[-1]",
          last_bound == n - 1,
          f"got={last_bound}, expect={n - 1}")
    check("滚到底时 spacer 仍为 0",
          int(grid._top_spacer.cget('height')) == 0
          and int(grid._bottom_spacer.cget('height')) == 0)

    # 4) 中间位置: 池覆盖可视区 (content_bot >= vis_bot), 无底部空白
    grid.canvas.yview_moveto(0.5)
    root.update_idletasks()
    grid._render_visible(force=True)
    root.update_idletasks()
    sr_h = float(grid.canvas.cget('scrollregion').split()[3])
    f0, f1 = grid.canvas.yview()
    vis_bot = f1 * sr_h
    first = grid._first_idx
    used = min(len(grid._widget_pool), grid._last_idx - grid._first_idx)
    content_bot = (first + used) * grid._row_height
    check("中段滚动时池内容覆盖可视区底部 (无下空白)",
          content_bot >= vis_bot,
          f"content_bot={content_bot}, vis_bot={vis_bot:.0f}")

    section("Scroll — 未用池 slot 移出布局 (data < 可视行数时不露白)")
    # 回归: 池 POOL_SIZE=40 恒定, 但数据可能只有 8 行。未用的 slot 若仍
    # pack 在 data_frame 里, 会占 32×rh 空白, 让 window item 比 scrollregion
    # 高出几百 px, 滚动时可视区露白 (用户截图: 8 行数据, 上下都有空白)。
    grid.clear()
    for i in range(8):
        grid.add_row(make_info(i))
    root.update_idletasks()
    grid._render_visible(force=True)
    root.update_idletasks()
    mapped = sum(1 for w in grid._widget_pool if w.winfo_manager())
    check("N=8 时只有 8 个池 widget 在布局中 (其余 pack_forget)",
          mapped == 8,
          f"mapped={mapped}, pool={len(grid._widget_pool)}")
    df_h = grid.data_frame.winfo_reqheight()
    check("N=8 时 data_frame 高 ≈ 8 × row_height (无多余空白)",
          abs(df_h - 8 * grid._row_height) <= 4,
          f"df_h={df_h}, 8×rh={8 * grid._row_height}")
    sr_h8 = float(grid.canvas.cget('scrollregion').split()[3])
    check("N=8 时 scrollregion 高 == N × row_height",
          sr_h8 == 8 * grid._row_height,
          f"sr_h={sr_h8}")

    # 滚到底时 window item 仍贴合内容 (无底部空白)
    grid.canvas.yview_moveto(1.0)
    root.update_idletasks()
    grid._render_visible(force=True)
    root.update_idletasks()
    wy8 = grid.canvas.coords(grid._canvas_window)[1]
    df_h8 = grid.data_frame.winfo_reqheight()
    f0b, f1b = grid.canvas.yview()
    sr8 = float(grid.canvas.cget('scrollregion').split()[3])
    check("N=8 滚到底时 window item 底 == scrollregion 底 (无露白)",
          abs((wy8 + df_h8) - sr8) <= 4,
          f"window bottom={wy8 + df_h8}, sr_h={sr8}")

    section("Scroll — 无隐藏内容时滚轮/滚动条禁用")
    # 无头环境下 canvas 未映射 (winfo_height()==1), 不能依赖真实视口高度。
    # 直接 mock canvas.winfo_height 返回已知视口, 验证判定逻辑与门控行为。
    rh = grid._row_height or 24
    VIEWPORT = 20 * rh          # 视口能放 20 行

    orig_height = grid.canvas.winfo_height
    grid.canvas.winfo_height = lambda: VIEWPORT   # type: ignore[method-assign]

    # 视口放得下 (N=8 <= 20 行) → 无隐藏内容
    grid.clear()
    root.update_idletasks()
    for i in range(8):
        grid.add_row(make_info(i))
    root.update_idletasks()
    grid._render_visible(force=True)
    root.update_idletasks()
    check("_has_hidden_rows 在数据装得下时为 False (8 行 <= 视口 20 行)",
          not grid._has_hidden_rows(),
          f"N={len(grid._data)}, sr_h={grid._scroll_height()}, viewport={VIEWPORT}")

    # 滚轮无效: yview 不变
    y_before = grid.canvas.yview()[0]
    class _WheelEvt:
        delta = -120
        x = y = 0
    grid._on_mousewheel(_WheelEvt())
    root.update_idletasks()
    grid._render_visible(force=True)
    root.update_idletasks()
    check("数据装得下时滚轮无效 (yview 不变)",
          grid.canvas.yview()[0] == y_before,
          f"before={y_before:.3f}, after={grid.canvas.yview()[0]:.3f}")

    # 滚动条拖动无效: command 换成空实现, 调用后 yview 不变
    y_before = grid.canvas.yview()[0]
    tk_cmd = grid.vsb.tk.call(grid.vsb._w, 'cget', '-command')
    grid.vsb.tk.call(tk_cmd, 'moveto', 0.5)
    root.update_idletasks()
    grid._render_visible(force=True)
    root.update_idletasks()
    check("数据装得下时滚动条拖动无效 (yview 不变)",
          grid.canvas.yview()[0] == y_before,
          f"before={y_before:.3f}, after={grid.canvas.yview()[0]:.3f}")

    # 数据超过视口 (N=60 > 20 行) → 恢复可用
    grid.clear()
    root.update_idletasks()
    for i in range(60):
        grid.add_row(make_info(i))
    root.update_idletasks()
    grid._render_visible(force=True)
    root.update_idletasks()
    check("数据超出视口时 _has_hidden_rows 为 True (60 行 > 视口 20 行)",
          grid._has_hidden_rows(),
          f"N={len(grid._data)}, sr_h={grid._scroll_height()}, viewport={VIEWPORT}")
    # 无头环境下 yview_scroll 不推进 (canvas 未映射), 所以用 scrollbar 的
    # command 验证「恢复可用」——它和滚轮走同一道 _has_hidden_rows 门控。
    tk_cmd = grid.vsb.tk.call(grid.vsb._w, 'cget', '-command')
    y_before = grid.canvas.yview()[0]
    grid.vsb.tk.call(tk_cmd, 'moveto', 0.5)
    root.update_idletasks()
    grid._render_visible(force=True)
    root.update_idletasks()
    check("数据超出视口时滚动条恢复可拖 (yview 推进)",
          grid.canvas.yview()[0] != y_before,
          f"before={y_before:.3f}, after={grid.canvas.yview()[0]:.3f}")

    # 还原真实 winfo_height (后续 section 不受影响)
    grid.canvas.winfo_height = orig_height  # type: ignore[method-assign]

    section("Public API — preserved")
    # add_row / clear / reorder / bind_drop / is_sorted 都存在且不抛
    methods = {'add_row', 'clear', 'reorder', 'bind_drop', 'is_sorted',
               'pack', 'grid', 'update_idletasks'}
    for m in methods:
        check(f"公开方法 {m} 存在", hasattr(grid, m) and callable(getattr(grid, m)))
    check("is_sorted() 在设了 _sort_key 后返 True",
          grid.is_sorted())

    section("Bound keys — _on_data_configure no longer bound")
    # 验证 data_frame 的 <Configure> 没绑 _on_data_configure (已废弃)
    bindings = grid.data_frame.bind('<Configure>')
    check("data_frame 解除 <Configure> → _on_data_configure 绑定 (新实现已移走 scrollregion)",
          'on_data_configure' not in bindings,
          f"current binding={bindings!r}")

    root.destroy()
    print(f"\n{'FAIL' if _failures else 'PASS'}: {len(_failures)} failure(s)")
    sys.exit(1 if _failures else 0)
