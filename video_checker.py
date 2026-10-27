"""视频码率检查器 - 检测视频文件码率是否达标"""

import os
import queue
import shutil
import sys
import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk
from typing import Callable
from tkinterdnd2 import TkinterDnD

# 纯逻辑已抽到 video_core.py。这里重新导出, 让既有的断言脚本
# (`from video_checker import X`) 不用改。
import video_core
from video_core import (  # noqa: F401
    EXTRACT_THUMB_W, FG_FAIL, FG_PASS, VIDEO_EXTENSIONS, VideoInfo,
    _SORTABLE_COLUMNS, _cell_fg, _extract_sort_key, _header_text_with_arrow,
    _next_sort_state, extract_frame, format_duration, format_file_size,
    format_title, get_ffmpeg_path, get_ffprobe_path, parse_threshold,
    parse_time_token, parse_video_info, run_ffprobe, scan_video_files,
    time_token_to_string, truncate_text,
)

DEFAULT_BITRATE_KBPS = 30000
DEFAULT_SAMPLE_RATE_KHZ = 48


def _font_measure(font: tkfont.Font) -> Callable[[str], int]:
    """把 tkfont.Font 适配成 truncate_text 要的 measure 可调用对象。

    .measure 的取值推迟到真正调用时 —— 现状 truncate_text 收到的是字体对象
    本身, 只有慢路径才会碰 .measure; 提前把 bound method 取出来会打破
    「没有 Tk root 时只测快路径」的用法 (见 bug-audit 断言脚本)。
    """
    return lambda s: font.measure(s)


def _parse_drop_data(data: str) -> list[str]:
    """解析 tkinterdnd2 拖拽事件 data 字段为文件路径列表。

    支持两种格式:
    - Windows: ``{file1} {file2}`` (大括号包路径, 空格分隔)
    - macOS / Linux: ``file1 file2`` (无大括号, 空格分隔)

    返回去除前后空白的文件路径列表。空字符串返回 ``[]``。

    已知限制: 含空格的路径在 macOS / Linux 格式下会被错误切分;
    这是 DND 格式本身的局限, 不在本函数能力范围。
    """
    import re
    text = data.strip()
    if not text:
        return []

    braced = re.findall(r'\{([^}]+)\}', text)
    if braced:
        return braced

    return text.split()


class VideoGridView:
    """自绘的可滚动表格，每个 cell 独立控制 fg。虚拟滚动版。"""

    # 颜色只有一份来源 (video_core.FG_PASS / FG_FAIL) —— 原来这里另抄了
    # 一份字面量, 将来改一处会静默不一致。类属性必须保留: 既有断言脚本
    # (`FG_PASS = VideoGridView.FG_PASS`) 依赖它。
    FG_PASS = video_core.FG_PASS
    FG_FAIL = video_core.FG_FAIL
    GRAB_WIDTH = 10
    # 池初始大小: 视区一行约占 24-26 px, 默认窗口 700 - 表头 ≈ 660, 默认可视
    # 行约 25 行; +2×BUFFER 上下预留 = 池 37. 取整 40, 留 ~30% 余量给超长文件名
    # 带来的行高膨胀、阈值放大场景。
    POOL_SIZE = 40
    VISIBLE_BUFFER = 6
    RESIZE_THROTTLE_MS = 33
    IDX_COL_WIDTH = 45   # 序号列宽 (固定, 不可拖拽, 不参与排序)

    def __init__(self, parent: tk.Widget, columns: dict[str, tuple[str, int]]):
        """
        parent: 父容器（ttk.Frame）
        columns: {'key': ('中文表头', width_in_pixels)}
        """
        self.parent = parent
        # 拷贝一份以避免拖动列宽时回写到外部 dict
        self.columns = {k: (h, w) for k, (h, w) in columns.items()}
        # 单一列宽真理来源。__init__ 从 columns 同步, 后续 _on_col_resize 更新。
        self._col_widths: dict[str, int] = {
            k: w for k, (h, w) in self.columns.items()
        }
        # 数据列表 (source of truth); 池 widget 仅绑定视图用, 不持数据真值。
        self._data: list[VideoInfo] = []
        self._widget_pool: list[ttk.Frame] = []
        # pool spacer (高度随时可调, 模拟 data 已滚走的部分)
        self._top_spacer: tk.Frame | None = None
        self._bottom_spacer: tk.Frame | None = None
        # 当前可见区间; _render_visible 用于判断是否需要真实重画。
        self._first_idx: int = 0
        self._last_idx: int = 0
        # 真实测得的行高 (首次 render 后用 widget_pool[0] 测量)
        self._row_height: int = 0
        # 列宽拖动中的瞬时状态
        self._resize_state: dict | None = None
        # 节流: 一次拖动期间的延迟重截断句柄与 pending 缓存
        self._resize_after_id: str | None = None
        self._pending_resize: tuple[str, int, int] | None = None
        # 排程: 多次事件合并到 after_idle 一次重画
        self._render_after_id: str | None = None
        # 字体 (cell 宽度测算用)
        self._cell_font = tkfont.Font(font=('TkDefaultFont', 9))
        # 悬停显示截断 cell 的完整文本 (长路径的唯一查看方式)
        self.tooltip = Tooltip(self.parent)
        # 排序状态: key=None 表示无排序; desc=False 为升序, True 为降序
        self._sort_key: str | None = None
        self._sort_desc: bool = False
        # 表头 Label 引用, 用于排序时改写文本 (附 ▲/▼)
        self._header_labels: dict[str, tk.Label] = {}
        self._build()

    def pack(self, **kwargs):
        self.outer.pack(**kwargs)

    def grid(self, **kwargs):
        self.outer.grid(**kwargs)

    def update_idletasks(self):
        self.outer.update_idletasks()

    def _build(self):
        """构建整个组件。"""
        # 主容器
        self.outer = ttk.Frame(self.parent)

        # 滚动区
        body = ttk.Frame(self.outer)
        body.pack(side='top', fill='both', expand=True)
        self.body = body

        # pack 顺序要紧:
        #   1) vsb 先占掉右侧那 15px, 之后表头 canvas 与正文 canvas 拿到
        #      **同样宽**的剩余空间 —— 宽度一样, 两边 grid 分配到的额外
        #      空间才相同, 列宽不会逐列漂移 (之前表头比正文宽 15px, 最右
        #      列差 14px)。代价: 竖直滚动条现在也覆盖表头那一行。
        #   2) 表头 canvas 必须先于正文 canvas pack —— side='top' 要先占
        #      顶部那条, 否则会被 side='left' 的正文 canvas 抢光。
        self.vsb = ttk.Scrollbar(body, orient='vertical')
        self.vsb.pack(side='right', fill='y')

        # 表头: 独立一个 canvas (见 _build_header)
        self._build_header()

        self.hsb = ttk.Scrollbar(body, orient='horizontal')
        self.hsb.pack(side='bottom', fill='x')

        self.canvas = tk.Canvas(body, highlightthickness=0)
        self.canvas.pack(side='left', fill='both', expand=True)
        # 备份原生 set, 在 command lambda 里同时更新 scrollbar 与排程 render。
        self._vsb_set = self.vsb.set
        self._hsb_set = self.hsb.set
        self.vsb.config(command=lambda *a: (
            self.canvas.yview(*a), self._schedule_render()))
        self.hsb.config(command=lambda *a: (
            self.canvas.xview(*a), self._schedule_render()))
        # xscrollcommand 走自己的包装: 正文横滚时表头要跟着动, 否则表头
        # 标注的列名跟正文对不上 (实测正文滚 225px 而表头纹丝不动)
        self.canvas.config(yscrollcommand=self.vsb.set,
                           xscrollcommand=self._on_body_xscroll)

        self.data_frame = ttk.Frame(self.canvas)
        self._canvas_window = self.canvas.create_window(
            (0, 0), window=self.data_frame, anchor='nw'
        )
        self.canvas.bind('<Configure>', self._on_canvas_configure)
        # _on_data_configure 由 _render_visible 显式设置 scrollregion, 不再绑定。
        # (原 handler 用 canvas.bbox('all') 在虚拟滚动下不可靠。)
        self._bind_mousewheel()

        # --- 虚拟滚动关键: 创建 spacer + 池 ---
        # data_frame 内层叠顺序: top_spacer, [pool widgets...], bottom_spacer
        # spacer 高度随时调整; 池 widget 视觉位置固定, 滚动 = 内容切换。
        self._top_spacer = tk.Frame(self.data_frame, height=0)
        self._top_spacer.pack(fill='x')
        # Tk Frame 的 height 需要 pack_propagate(False) 才能强制, 但这里因为
        # frame 内无 child, height 配置直接生效。
        for _ in range(self.POOL_SIZE):
            w = self._create_pool_widget()
            # 预先建好但**不映射**: 用到哪行才 pack 谁 (见 _render_visible)。
            # 否则从没扫描过的 app 也有 40 行 × 11 = 440 个映射着的子窗口,
            # 最小化恢复时 Windows 要逐个重绘它们 → 表格区先露底。
            # winfo_reqheight 对未映射的 widget 照样返回真实行高, 不影响测量。
            w.pack_forget()
        self._bottom_spacer = tk.Frame(self.data_frame, height=0)
        self._bottom_spacer.pack(fill='x')
        # 触发一次 idle 等 widget mapped 后测量实际行高
        self.canvas.update_idletasks()
        self._measure_row_height()
        # canvas 不会自动贴合 window item 的高度, 表头得显式给高度
        self._header_h = self.header_frame.winfo_reqheight()
        self.header_canvas.configure(height=self._header_h)

    def _build_header(self):
        """表头: 独立一个 canvas, 内容是 header_frame (作为 window item)。

        为什么不让表头直接当正文 canvas 的兄弟 widget:
          1) 横向滚动时表头纹丝不动 → 表头标注的列名与正文对不上 (实测把
             一列拖宽到超出窗口, 正文滚了 225px, 表头没动)。
          2) 表头比正文 canvas 宽 15px (竖直滚动条占的), 两边 grid 分配
             到的额外空间不同 → 列宽逐列漂移, 最右列差 14px (窗口越宽
             差得越多)。
        放进独立 canvas 后: 宽度与正文 canvas 相同 (靠 _build 的 pack 顺序
        保证), 两边 grid 分配一致 → Δ=0; 横向滚动同步 xview → 始终指对列。
        """
        self.header_canvas = tk.Canvas(self.body, highlightthickness=0)
        self.header_canvas.pack(side='top', fill='x')
        self.header_frame = ttk.Frame(self.header_canvas)
        self._header_window = self.header_canvas.create_window(
            (0, 0), window=self.header_frame, anchor='nw')
        self._header_h = 0

        # grid 列布局: col0 = 序号 (固定), 然后数据列沿用「偶数=标签, 奇数=grab」
        # 但整体右移 1 位 → 数据列 i 的标签在 col 2*i+1, grab 在 col 2*i+2。
        idx_lbl = tk.Label(
            self.header_frame,
            text="序号",
            font=('TkDefaultFont', 9),
            anchor='center',
            padx=4, pady=2,
            relief='raised', bd=1,
        )
        idx_lbl.grid(row=0, column=0, sticky='nsew')
        self.header_frame.grid_columnconfigure(0, minsize=self.IDX_COL_WIDTH, weight=0)

        keys = list(self.columns.keys())
        n = len(keys)
        for i, key in enumerate(keys):
            heading, width = self.columns[key]
            col_idx = i * 2 + 1

            anchor = self._column_anchor(key)
            display_heading = self._truncate_text(heading, width)
            sortable = key in _SORTABLE_COLUMNS
            lbl = tk.Label(
                self.header_frame,
                text=display_heading,
                font=('TkDefaultFont', 9),
                anchor=anchor,
                padx=4, pady=2,
                relief='raised', bd=1,
                cursor='hand2' if sortable else None,
            )
            lbl.grid(row=0, column=col_idx, sticky='nsew')
            self.header_frame.grid_columnconfigure(col_idx, minsize=width, weight=1)
            self._header_labels[key] = lbl
            # 可排序列点击 → 触发排序; 拖拽手柄 (grab) 仍可独立 resize
            if sortable:
                lbl.bind('<Button-1>', lambda _e, k=key: self._on_header_click(k))

            # 最后一列不放手柄
            if i < n - 1:
                # grab 是 GRAB_WIDTH 宽的命中热区；line 是 1px 可见分隔线（占左 1px），
                # 与左数据列右边界对齐。事件同时绑到 grab 和 line，否则点 line 上会被 line 吃掉。
                grab = tk.Frame(self.header_frame, cursor='sb_h_double_arrow')
                # sticky='nsew' 让 grab 填满整列（命中区 = GRAB_WIDTH）；
                # 否则空 Frame 默认只有 1px，列内其余 9px 没有 widget 接收事件。
                grab.grid(row=0, column=col_idx + 1, sticky='nsew')
                # weight=0: 不参与拉伸/压缩, 始终保持 GRAB_WIDTH 热区
                self.header_frame.grid_columnconfigure(col_idx + 1, minsize=self.GRAB_WIDTH, weight=0)

                line = tk.Frame(grab, width=1, bg='#a0a0a0', cursor='sb_h_double_arrow')
                line.pack(side='left', fill='y')

                on_press = lambda e, k=key: self._start_col_resize(e, k)
                on_drag = lambda e, k=key: self._on_col_resize(e, k)
                on_release = lambda _e: self._end_col_resize()
                for w in (grab, line):
                    w.bind('<Button-1>', on_press)
                    w.bind('<B1-Motion>', on_drag)
                    w.bind('<ButtonRelease-1>', on_release)

    # ---- 表头与正文的同步 ----

    def _on_body_xscroll(self, first, last=None):
        """正文横向滚动 → 表头跟着动 (否则表头指错列)。"""
        self._hsb_set(first, last)
        self._sync_header_x(first)

    def _sync_header_x(self, first=None) -> None:
        """表头 canvas 的横向位置对齐到正文。

        两个 canvas 的 scrollregion 宽度与视口宽度都相同, 所以同一个
        fraction 对应同一个像素偏移。
        """
        try:
            if first is None:
                first = self.canvas.xview()[0]
            self.header_canvas.xview_moveto(first)
        except tk.TclError:
            pass

    def _sync_header_geometry(self, width: int) -> None:
        """表头与正文同宽 + 同样的 scrollregion 宽度。

        宽度一致 → 两边 grid 分配到的列宽完全相同 (Δ=0)。
        """
        try:
            self.header_canvas.itemconfigure(self._header_window, width=width)
            self.header_canvas.configure(scrollregion=(
                0, 0, width, self._header_h))
        except tk.TclError:
            pass
        self._sync_header_x()

    def _create_pool_widget(self) -> ttk.Frame:
        """创建一个池 widget: 1 ttk.Frame 内含 12 tk.Label 单元格, 不绑定数据。

        池 widget 始终 pack 在 top_spacer 与 bottom_spacer 之间; 滚动期间
        通过更新各 widget 的 cell 文本与 fg 来切换显示哪一行数据。
        """
        row_frame = ttk.Frame(self.data_frame)
        # pack 时不放在最前/最后; 而是插在 _bottom_spacer 之前 (保持顺序:
        # top_spacer | pool[0..] | bottom_spacer)。
        row_frame.pack(fill='x', before=self._bottom_spacer)
        row_frame._info = None               # 当前绑定的 VideoInfo
        row_frame._bound_data_idx: int | None = None
        # (key, cell_label) — 列宽变化时按 full_text 重新截断
        # col 0 = 序号 cell (固定宽, 与表头 col0 对齐; 不进 _cells 列表, 单独存 _idx_cell)
        idx_cell = tk.Label(
            row_frame, text='', anchor='center', padx=4, pady=2, fg='black',
        )
        idx_cell.grid(row=0, column=0, sticky='nsew')
        row_frame.grid_columnconfigure(0, minsize=self.IDX_COL_WIDTH, weight=0)
        row_frame._idx_cell = idx_cell

        row_cells: list[tuple[str, tk.Label]] = []
        n = len(self.columns)
        for i, (key, (_heading, _width)) in enumerate(self.columns.items()):
            anchor = self._column_anchor(key)
            cell = tk.Label(
                row_frame,
                text='',
                anchor=anchor,
                padx=4, pady=2,
                fg='black',
            )
            cell.grid(row=0, column=i * 2 + 1, sticky='nsew')
            # tooltip 用: 记录完整值与是否被截断, 鼠标移动时按需弹窗。
            # <Motion> 和 <Leave> 必须成对绑 (缺一个就粘住/闪烁, 见 Tooltip
            # docstring)。绑定在建池时一次完成 (事件不向父 widget 冒泡, 没法在
            # 行 frame 上统一绑); _render_visible 只改这两个属性, 不重绑。
            cell._full_text = ''
            cell._truncated = False
            cell.bind('<Motion>', self.tooltip.on_motion)
            cell.bind('<Leave>', self.tooltip.on_leave)
            # 用 _col_widths (而非 self.columns) 同步列宽真理来源
            row_frame.grid_columnconfigure(i * 2 + 1, minsize=self._col_widths[key], weight=1)
            row_cells.append((key, cell))
            if i < n - 1:
                # 与表头 grab 列同宽, 保证列边界对齐; 不放 widget 仅占空间
                # weight=0: 不参与拉伸/压缩, 始终保持 GRAB_WIDTH
                row_frame.grid_columnconfigure(i * 2 + 2, minsize=self.GRAB_WIDTH, weight=0)
        row_frame._cells = row_cells
        self._widget_pool.append(row_frame)
        return row_frame

    def _grow_pool(self, need: int) -> None:
        """扩张池到至少需要 need 个 widget。"""
        while len(self._widget_pool) < need:
            self._create_pool_widget()

    def _measure_row_height(self) -> None:
        """首次测量实际行高。winfo_reqheight 在未 mapped 时返 1, 故配合 idle。"""
        if not self._widget_pool:
            return
        try:
            h = self._widget_pool[0].winfo_reqheight()
        except tk.TclError:
            h = 0
        if h and h > 1:
            self._row_height = h
        # 兜底保持已有 fallback 值 (24-26 px)

    def _compute_total_width(self) -> int:
        """列总宽 + spacer 总宽 (含固定宽的序号列)."""
        if not self._col_widths:
            return 0
        cols = self.IDX_COL_WIDTH + sum(self._col_widths.values())
        spaces = max(0, len(self._col_widths) - 1) * self.GRAB_WIDTH
        return cols + spaces

    def _scroll_height(self) -> int:
        """可滚动总高度 = 数据总行数 × 行高.

        不用 top/bottom spacer 表达滚动: spacer 高度依赖 first/last, 而
        first/last 又从 scrollregion 位置反推, 会形成正反馈 (滚动越深
        spacer 越大, 把内容越推越下, 顶部露出空白)。
        改用 canvas window item 的 y 坐标偏移 (见 _render_visible),
        scrollregion 高度恒为 N × row_height, 与池大小无关。
        """
        n = len(self._data)
        if n <= 0 or self._row_height <= 0:
            return 0
        return n * self._row_height

    def _start_col_resize(self, event, key: str):
        self._resize_state = {
            'key': key,
            'start_x': event.x_root,
            'start_width': self.columns[key][1],
        }

    def _on_col_resize(self, event, key: str):
        if self._resize_state is None or self._resize_state['key'] != key:
            return
        dx = event.x_root - self._resize_state['start_x']
        new_width = max(20, int(self._resize_state['start_width'] + dx))
        if new_width == self.columns[key][1]:
            return

        # === 即时 (cheap): 更新列宽 dict + header + 池 widget grid minsize ===
        heading = self.columns[key][0]
        self.columns[key] = (heading, new_width)
        self._col_widths[key] = new_width
        keys = list(self.columns.keys())
        data_idx = keys.index(key)
        header_col_idx = data_idx * 2 + 1   # +1: col0 预留给固定宽序号列
        self.header_frame.grid_columnconfigure(header_col_idx, minsize=new_width)
        # Header label text may re-truncate (列缩短时)
        header_lbl = self._header_labels[key]
        base_text = self.columns[key][0]
        is_active = (key == self._sort_key)
        arrow = _header_text_with_arrow(base_text, is_active, self._sort_desc)
        new_header_text = self._truncate_text(arrow, new_width)
        if header_lbl.cget('text') != new_header_text:
            header_lbl.config(text=new_header_text)
        # 池 widget: 即使未 mapped 也要更新 minsize (Tk 持久化 grid_columnconfigure
        # 选项; 否则下次 mapped 时 cell 宽度错位)。
        for widget in self._widget_pool:
            widget.grid_columnconfigure(data_idx * 2 + 1, minsize=new_width)  # +1 序号列偏移
        # scrollregion 横向范围也随之变化
        total_w = max(self.canvas.winfo_width(), self._compute_total_width())
        self.canvas.configure(scrollregion=(
            0, 0, total_w, self._scroll_height(),
        ))
        # 表头跟着变: 同宽 + 同样的 scrollregion 宽度
        self._sync_header_geometry(total_w)

        # === 节流 (33ms): cell 文本重截断合并到 after() ===
        self._pending_resize = (key, data_idx, new_width)
        if self._resize_after_id is not None:
            return  # 已 schedule, 后续 _pending_resize 覆盖即可
        self._resize_after_id = self.canvas.after(
            self.RESIZE_THROTTLE_MS, self._apply_pending_resize)

    def _apply_pending_resize(self):
        """应用 pending 列宽重截断。仅对 mapped (可见) widget 做 cell 文本写入。"""
        self._resize_after_id = None
        pending = self._pending_resize
        if pending is None:
            return
        self._pending_resize = None
        key, data_idx, new_width = pending
        for widget in self._widget_pool:
            if not widget.winfo_ismapped():
                continue
            k, cell = widget._cells[data_idx]
            # bound_data_idx 未绑时不重画 cell (渲染发生在 _render_visible)
            if widget._bound_data_idx is None:
                continue
            info = widget._info
            full_text = self._format_cell(info, k)
            new_text = self._truncate_text(full_text, new_width)
            if cell.cget('text') != new_text:
                cell.config(text=new_text)

    def _end_col_resize(self):
        # 立刻 flush pending; 避免 33ms 延迟到释放后才完成最后一次重截断
        if self._resize_after_id is not None:
            try:
                self.canvas.after_cancel(self._resize_after_id)
            except tk.TclError:
                pass  # widget 已销毁
            self._resize_after_id = None
        self._apply_pending_resize()
        self._resize_state = None

    def _truncate_text(self, text: str, col_width: int) -> str:
        """委托模块级 truncate_text（弹窗也用同一个实现）。"""
        return truncate_text(text, col_width, _font_measure(self._cell_font))

    @staticmethod
    def _column_anchor(key: str) -> str:
        if key == 'title':
            return 'w'
        return 'center'

    def _on_canvas_configure(self, event):
        # 横向: 让 inner frame 至少和 canvas 等宽 (填满); 但当内容 (列总 minsize)
        # 比 canvas 宽时, 保持 natural 宽度, 让横向滚动条接管。
        # 虚拟滚动下 data_frame 实际宽 = 池 widget 高度累计, 故用 _compute_total_width
        # 与 canvas 等宽取大。
        natural = self._compute_total_width()
        width = max(event.width, natural)
        self.canvas.itemconfigure(self._canvas_window, width=width)
        # 表头同宽 + 同样的 scrollregion 宽度 → 两边 grid 分配一致 (Δ=0)
        self._sync_header_geometry(width)
        # 窗口高度变化 → 一页能放几行变了 → 重新判断有无隐藏内容
        self._sync_scroll_enabled()
        # 窗口 resize 后 visible_count 可能变化, 重新计算可见区间
        self._schedule_render()

    def _bind_mousewheel(self):
        self.canvas.bind('<Enter>', lambda _e: self._wheel_bind_id())
        self.canvas.bind('<Leave>', lambda _e: self._unbind_mousewheel())

    def _wheel_bind_id(self):
        # macOS / Windows
        self.canvas.bind_all('<MouseWheel>', self._on_mousewheel)
        # Linux (X11)
        self.canvas.bind_all('<Button-4>', self._on_mousewheel_linux)
        self.canvas.bind_all('<Button-5>', self._on_mousewheel_linux)

    def _unbind_mousewheel(self):
        self.canvas.unbind_all('<MouseWheel>')
        self.canvas.unbind_all('<Button-4>')
        self.canvas.unbind_all('<Button-5>')

    def _has_hidden_rows(self) -> bool:
        """是否还有被滚动隐藏的行 (内容总高 > 视口高).

        内容未超出视口时 (如视口能放 40 行但只有 30 行数据),
        滚轮和滚动条都应禁用 — 没有可滚的内容。
        """
        canvas_h = self.canvas.winfo_height()
        if canvas_h <= 1:
            canvas_h = self.canvas.winfo_reqheight()
        if canvas_h <= 1:
            return False
        return self._scroll_height() > canvas_h

    def _sync_scroll_enabled(self) -> None:
        """按有无隐藏内容同步滚动条: 无内容可滚时 command 置空 (拖不动).

        ttk.Scrollbar 没有 -state 选项 (ttk 样式控制), 所以用
        command 换成空实现来真正禁用拖动 — 否则用户仍能拖 thumb,
        只是 yview 立即被 clamp 回 0, 视觉上像是"拖了没反应"。
        """
        if not hasattr(self, 'vsb'):
            return
        if self._has_hidden_rows():
            self.vsb.config(command=lambda *a: (
                self.canvas.yview(*a), self._schedule_render()))
        else:
            self.vsb.config(command=lambda *a: None)

    def _on_mousewheel(self, event):
        if not self._has_hidden_rows():
            return  # 无隐藏内容 → 滚轮无效
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')
        # yview 改变后哪些 data 在可见区需要重算 → 排程 render
        self._schedule_render()

    def _on_mousewheel_linux(self, event):
        if not self._has_hidden_rows():
            return  # 无隐藏内容 → 滚轮无效
        delta = -1 if event.num == 4 else 1
        self.canvas.yview_scroll(delta, 'units')
        self._schedule_render()

    # === 虚拟滚动核心 ===

    def _schedule_render(self):
        """排程在 idle 时调用 _render_visible; 多次调用合并到一次。"""
        if self._render_after_id is not None:
            return
        self._render_after_id = self.canvas.after_idle(self._do_scheduled_render)

    def _do_scheduled_render(self):
        self._render_after_id = None
        try:
            self._render_visible()
        except tk.TclError:
            pass  # widget 已被销毁 (扫描中关闭窗口等场景)

    def _render_visible(self, force: bool = False):
        """按 canvas 当前位置 + 测量行高 计算可见区间, 重新绑定池 widget。

        force=True 时跳过 (first,last) 比较, 用于 reorder / clear 等显式 invalidate。
        """
        n = len(self._data)
        if n == 0:
            # 空数据: top/bottom spacer 高度 = 0, scrollregion 高 = 0,
            # 池 widget 全部 map 但 cell 文本已被 _clear 留空。
            self._top_spacer.configure(height=0)
            self._bottom_spacer.configure(height=0)
            self.canvas.yview_moveto(0)
            self.canvas.configure(scrollregion=(
                0, 0, self.canvas.winfo_width(), 0,
            ))
            self._sync_header_geometry(self.canvas.winfo_width())
            self._first_idx, self._last_idx = 0, 0
            self._sync_scroll_enabled()  # 无数据 → 滚动条禁用
            # 空表格不该还留着映射着的行 (见 _unmap_pool 的注释)
            self._unmap_pool()
            return

        # 行高兜底: 若首次 render 未拿到 (mapped 前), 用 26 px
        canvas_h = self.canvas.winfo_height()
        if canvas_h <= 1:
            canvas_h = 600  # 未 mapped 的窗口兜底
        if self._row_height <= 0:
            self._measure_row_height()
            if self._row_height <= 0:
                self._row_height = 26

        visible = max(1, canvas_h // self._row_height)
        # 可视区顶部在 scrollregion 中的像素位置
        first_frac, _ = self.canvas.yview()
        first_frac = max(0.0, min(1.0, first_frac))
        sr_h = self._scroll_height()
        if sr_h > canvas_h:
            vis_top = first_frac * sr_h
        else:
            vis_top = 0.0
        # 可视区顶部对应的数据行号 (除掉 top_spacer 的偏移)
        raw_first = int(vis_top // self._row_height)
        # 上下各预留 buffer 行, 避免滚动瞬间空白行
        first = max(0, raw_first - self.VISIBLE_BUFFER)
        last = min(n, raw_first + visible + self.VISIBLE_BUFFER)

        if not force and first == self._first_idx and last == self._last_idx:
            return  # 区间无变化, 跳过 (无 main-thread 工作量)

        self._first_idx, self._last_idx = first, last

        # 池扩张 (窗口放大或初次 render)
        needed = last - first
        if needed > len(self._widget_pool):
            self._grow_pool(needed)

        # 不用 spacer 表达滚动 (会与 first/last 形成正反馈导致顶部空白);
        # 改用 canvas window item 的 y 坐标偏移: 数据窗整体下移 first*rh。
        self._top_spacer.configure(height=0)
        self._bottom_spacer.configure(height=0)
        self.canvas.coords(self._canvas_window, 0, first * self._row_height)

        # 池 widget slot → data 绑定 (倒序填充, 视觉顺序不变)
        used = last - first
        for slot, data_idx in enumerate(range(first, last)):
            widget = self._widget_pool[slot]
            # 用到的 slot 必须在布局里 (上次可能被 pack_forget 过)
            if not widget.winfo_manager():
                widget.pack(fill='x', before=self._bottom_spacer)
            info = self._data[data_idx]
            # 未变化就跳过 cell 重写; 这是热路径, 大量命中此分支。
            if (widget._bound_data_idx == data_idx
                    and widget._info is info):
                continue
            widget._bound_data_idx = data_idx
            widget._info = info
            # 序号列: 显示 1-based 全局行号 (data_idx + 1)
            idx_text = str(data_idx + 1)
            if widget._idx_cell.cget('text') != idx_text:
                widget._idx_cell.config(text=idx_text)
            for key, cell in widget._cells:
                full_text = self._format_cell(info, key)
                width = self._col_widths[key]
                new_text = self._truncate_text(full_text, width)
                # tooltip 靠这两个属性判断要不要弹 (只在真被截断时弹)
                cell._full_text = full_text
                cell._truncated = (new_text != full_text)
                if cell.cget('text') != new_text:
                    cell.config(text=new_text)
                fg = _cell_fg(info, key)
                new_fg = fg if fg is not None else 'black'
                if cell.cget('fg') != new_fg:
                    cell.config(fg=new_fg)

        # 用不到的池 widget 必须移出布局: 否则它们仍占 data_frame 高度
        # (POOL_SIZE × rh), 让 window item 比 scrollregion 高出几百 px,
        # 滚动时可视区露白。N < 可视行数时最明显 (池 40 vs 数据 8)。
        for slot in range(used, len(self._widget_pool)):
            widget = self._widget_pool[slot]
            if widget.winfo_manager():
                widget.pack_forget()
            widget._bound_data_idx = None
            widget._info = None

        # scrollregion 高 = N × row_height (恒定, 与池大小无关; 未用 slot 已
        # pack_forget, 所以 window item 实际高恰好等于可视行数 × rh,
        # 滚动时不会出现露白)。
        total_h = self._scroll_height()
        total_w = max(self.canvas.winfo_width(), self._compute_total_width())
        self.canvas.configure(scrollregion=(0, 0, total_w, total_h))
        # 表头跟着: 同宽 + 同样的 scrollregion 宽度 (Δ=0 的关键)
        self._sync_header_geometry(total_w)
        self._sync_scroll_enabled()  # 无隐藏内容 → 滚动条置灰

    def _unmap_pool(self) -> None:
        """把池里所有行移出布局 (unmap), 并清掉它们的绑定关系。

        没在用的行如果还映射着, 它们仍然是真实的子窗口 —— 窗口最小化再
        恢复时, Windows 要逐个重新绘制它们, 表格区在画完之前是露底的
        (用户报的现象)。表格空的时候一行都不该留着。
        """
        for widget in self._widget_pool:
            if widget.winfo_manager():
                widget.pack_forget()
            widget._bound_data_idx = None
            widget._info = None

    def clear(self):
        """清空所有数据行，滚动条复位。"""
        self.tooltip.hide()   # 否则 tooltip 会带着旧文本留在空列表上
        self._data.clear()
        for widget in self._widget_pool:
            widget._idx_cell.config(text='', fg='black')  # 序号列也要清
            for _key, cell in widget._cells:
                cell.config(text='', fg='black')
        # 池 widget 全部移出布局 (见 _unmap_pool 的注释): 只清文本不
        # unmap 的话, 扫过 60 个文件再清除记录, canvas 里仍映射着
        # 27 行 × 11 = 297 个子窗口, 恢复时照样要逐个重绘。
        self._unmap_pool()
        self.canvas.yview_moveto(0)
        # 同步 spacing / scrollregion
        self._top_spacer.configure(height=0)
        self._bottom_spacer.configure(height=0)
        self.canvas.configure(scrollregion=(
            0, 0, self.canvas.winfo_width(), 0,
        ))
        self._sync_header_geometry(self.canvas.winfo_width())
        self._first_idx = self._last_idx = 0
        self._sync_scroll_enabled()  # 无数据 → 滚动条禁用
        # 不需要 _render_visible (上面已经手工清空且 n=0 时 _render 也是 noop)

    def add_row(self, info: VideoInfo):
        """往表格里追加一行。虚拟滚动: 仅 append 到 _data 列表 + 排程 render。"""
        self._data.append(info)
        # 如果新行落在可见区间, 重画; 否则仅延后到下一次 _render_visible 调用
        # (滚动到位时)。排程 after_idle 合并多次 add_row。
        self._schedule_render()

    @staticmethod
    def _format_cell(info: VideoInfo, key: str) -> str:
        """把 VideoInfo 字段映射成 cell 显示文本。"""
        if key == 'title':
            return format_title(info)
        if key == 'bitrate':
            return f"{info.bitrate_kbps:.0f}"
        if key == 'spec':
            return f"{info.container_format}/{info.video_codec}/{info.audio_codec}"
        if key == 'result':
            return "达标" if info.is_passing else "不达标"
        return {
            'resolution': info.resolution,
            'frame_rate': info.frame_rate,
            'audio_channels': info.audio_channels,
            'audio_sample_rate': info.audio_sample_rate,
            'duration': info.duration,
            'file_size': info.file_size,
        }[key]

    def bind_drop(self, callback):
        """把拖拽事件绑定到 outer（覆盖表头 + 画布，空表也能接住）。"""
        self.outer.drop_target_register('DND_Files')
        self.outer.dnd_bind('<<Drop>>', callback)

    # --- 排序 ---

    def is_sorted(self) -> bool:
        """是否处于排序状态 (供 App 在扫描完成后决定是否 reorder)。"""
        return self._sort_key is not None

    def _on_header_click(self, key: str):
        """列表头点击: 3 态循环 + 跨列重置到升序。"""
        self._sort_key, self._sort_desc = _next_sort_state(
            self._sort_key, self._sort_desc, key,
        )
        self._refresh_headers()
        self.reorder()

    def _refresh_headers(self):
        """重写所有表头 Label 文本: 活动列附 ▲/▼, 其他列原样。"""
        for key, lbl in self._header_labels.items():
            base_text, width = self.columns[key]
            is_active = (key == self._sort_key)
            arrow = _header_text_with_arrow(base_text, is_active, self._sort_desc)
            new_text = self._truncate_text(arrow, width)
            if lbl.cget('text') != new_text:
                lbl.config(text=new_text)

    def reorder(self):
        """按当前排序状态重排现有行。无排序状态或 0 行时为 no-op。

        虚拟滚动: 原地排序 _data 后强制重画 (不销毁 widget), 性能优于旧版。
        """
        if self._sort_key is None or not self._data:
            return
        # list.sort 稳定, 同级保留插入序
        self._data.sort(
            key=lambda i: _extract_sort_key(i, self._sort_key),
            reverse=self._sort_desc,
        )
        self._render_visible(force=True)




class Tooltip:
    """悬停提示：目标 widget 声明了 _truncated=True 时显示它的 _full_text。

    用法: 每个 cell 都要绑**两个**事件, 少一个都会出问题:
        cell.bind('<Motion>', tooltip.on_motion)
        cell.bind('<Leave>', tooltip.on_leave)
    - <Motion>: 弹出 / 更新。移到没有绑定的 widget (表头 / 滚动条 / 状态栏 /
      网格外的任何地方) 时它不再跑, 所以光有它不够。
    - <Leave>: 唯一的关闭路径。缺了它浮窗会带着旧文本一直挂着, 而它画在鼠标
      附近, 看着像是属于鼠标当前所在的位置 —— 陈旧信息, 会误导。
      关闭走 after_idle 延迟 + show() 复用窗口, 所以 cell 间移动不闪。

    判定截断与取完整文本都由 cell 上的 _truncated / _full_text 负责,
    Tooltip 不关心文本是怎么算出来的。

    Tk 事件只沿 bindtags 派发, **不向父 widget 冒泡** —— 所以必须逐个 cell
    绑定, 绑在 canvas / 行 frame 上覆盖不到停在子 cell 上的鼠标。
    """

    def __init__(self, owner: tk.Misc):
        self._owner = owner
        self.win: tk.Toplevel | None = None
        self._label: tk.Label | None = None
        self._cell = None
        self._leave_after_id = None

    def on_leave(self, event) -> None:
        """鼠标离开某个 cell。延迟关闭: 同一批事件里若紧接着移到另一个 cell,
        那个 cell 的 <Motion> 会取消这次关闭 —— 直接 hide 会在 cell→cell
        移动时销毁重建 Toplevel, 看起来闪烁。"""
        if self._leave_after_id is None:
            self._leave_after_id = self._owner.after_idle(self._hide_now)

    def _hide_now(self) -> None:
        self._leave_after_id = None
        self.hide()

    def on_motion(self, event) -> None:
        cell = event.widget
        # 移回某个 cell → 取消上一次 <Leave> 排的延迟关闭
        if self._leave_after_id is not None:
            self._owner.after_cancel(self._leave_after_id)
            self._leave_after_id = None
        if not getattr(cell, '_truncated', False):
            self.hide()
            return
        if self.win is not None and self._cell is cell:
            # 同一 cell 内的移动不重建 —— 但要重新抬一次: lift() 只抬应用内的
            # sibling, 应用重新获得焦点时主窗口会被提到前面, tooltip 就落到它
            # 后面 (不像 -topmost 那样常驻最前)。不抬的话用户切回来后即使
            # 挪了鼠标也看不到 tooltip。
            self.win.lift()
            return
        self.show(cell)

    def show(self, cell) -> None:
        # 指针坐标取自 owner, 新建/复用两条路径用同一个来源 —— 复用时新
        # Toplevel 还不存在, 混用两个来源位置会不对。
        x = self._owner.winfo_pointerx() + 12
        y = self._owner.winfo_pointery() + 18
        if self.win is not None and self._label is not None:
            # 复用: 鼠标扫过列表时不停跨越 cell 边界, 每次销毁+新建 Toplevel
            # 实测约 4.4ms/次 (Windows 的窗口创建通常更慢) —— 扫 20 行就是几百
            # 毫秒的纯窗口创建开销。改成只改文本 + 重定位。
            self._label.config(text=cell._full_text)
            self.win.geometry(f"+{x}+{y}")
            self.win.lift()   # 见下面 win.lift() 的注释
            self._cell = cell
            return
        win = tk.Toplevel(self._owner)
        win.overrideredirect(True)
        # 先把 self.win 挂上: 下面建 Label / pack 若抛异常 (比如 teardown
        # 期间), 要靠它回收窗口 —— 否则屏幕上留下一个没人管的孤儿 Toplevel。
        # self._cell 留在最后 —— 失败时不该指向一个半成品。
        self.win = win
        try:
            self._label = tk.Label(
                win, text=cell._full_text, font=('TkDefaultFont', 9),
                bg='#ffffe0', fg='black', relief='solid', bd=1,
                justify='left', padx=4, pady=2,
            )
            self._label.pack()
            win.geometry(f"+{x}+{y}")
            # 用 lift() 而不是 attributes('-topmost', True):
            # -topmost 是**进程无关**的, 浮窗会浮在所有应用之上 —— Alt+Tab
            # 切走后鼠标不动, <Leave> 不触发, tooltip 就浮在刚切过去的那个
            # 应用上面。lift() 只在本应用内抬 sibling, 失焦后自然落到后面。
            # 实测 (macOS/Aqua, Tk 9.0): lift() 能把 overrideredirect 的
            # Toplevel 提到应用内最前, 且不抢焦点 (焦点仍在原 widget 上)。
            win.lift()
        except Exception:
            self.hide()   # 建到一半失败也要回收, 不留孤儿窗口
            raise
        self._cell = cell

    def hide(self) -> None:
        if self.win is not None:
            self.win.destroy()
            self.win = None
        # 必须一起清: 否则下次 show() 会去 config 一个已销毁的 label
        self._label = None
        self._cell = None


class EnlargeDialog:
    """点击缩略图后弹出的原图查看窗口.

    加载磁盘上的 PNG (extract_frame 写的 480x270 大图) 到 Toplevel,
    按图实际尺寸 + padding 调整窗口大小。
    """

    def __init__(
        self, parent: tk.Toplevel,
        image_path: str, title: str,
    ):
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.transient(parent)
        self.top.configure(bg='#202020')
        # 持有 PhotoImage 防 GC
        self._photo: tk.PhotoImage = tk.PhotoImage(file=image_path)
        img_label = tk.Label(self.top, image=self._photo, bg='#202020')
        img_label.pack(padx=12, pady=(12, 6))
        # 路径信息
        path_lbl = tk.Label(
            self.top, text=image_path, fg='#cccccc', bg='#202020',
            font=('TkDefaultFont', 9), wraplength=max(self._photo.width(), 400),
        )
        path_lbl.pack(padx=12)
        ttk.Button(self.top, text="关闭", command=self.top.destroy).pack(pady=(6, 12))
        self.top.update_idletasks()
        # 按图实际尺寸 + padding 调整窗口
        w = self._photo.width() + 24
        h = self._photo.height() + 90
        self.top.geometry(f"{w}x{h}")


class SnapshotProgressDialog:
    """定点截图单窗口界面 (代替原 弹窗+输入对话框 两段式):
       顶部: 开始/截图中 按钮 + 时间点输入栏 (一行, 默认 2 个, 可加)
       中间: 文件 × 时间点 表格 (canvas + cells, 与原版布局相同)
       底部: 状态文字 + 进度条 (从原版顶部挪下来)

    状态机:
      idle:   初始 / 取消后 — 按钮「开始截图」
      running: worker 跑 — 按钮「截图中」(点击取消当前扫描)
      done:   全部跑完 — 按钮「开始截图」(重抽)

    关闭用窗口 X 按钮; running 时点 X 弹确认。
    """

    THUMB_DISPLAY_W = EXTRACT_THUMB_W // 2  # cell 显示宽度 (subsample 2 后) = 320
    THUMB_FULL_W = EXTRACT_THUMB_W          # extract_frame 写盘宽度; click-enlarge 也用这个尺寸
    THUMB_SUBSAMPLE = 2                     # 640 / 320 = 2
    ROW_BG_PASS = '#E8F5E9'   # 浅绿 (保留常量, 供未来可选开启)
    ROW_BG_FAIL = '#FFEBEE'   # 浅红
    # 文字色: 与主界面 VideoGridView.FG_PASS / FG_FAIL 同源 (都取自
    # video_core, 不再各抄一份字面量)。主界面不画整行底色, 只用 fg
    # 区分达标/不达标; 这里对齐该做法。
    FG_PASS = video_core.FG_PASS   # 绿字 (达标)
    FG_FAIL = video_core.FG_FAIL   # 红字 (不达标)
    TITLE_COL_WIDTH = 250     # 与主表格 title 列宽一致
    IDX_COL_WIDTH = 45       # 序号列宽 (固定, 不随时间点数量变化)

    def __init__(
        self, parent: tk.Tk,
        files: list, output_dir: str,
        default_time_tokens: tuple[str, ...] = ("00:00:07:24", "00:00:08:00"),
    ):
        """files: 已扫描好的 VideoInfo 列表; output_dir: 截图保存目录;
        default_time_tokens: 初始时间点列表 (默认 2 个, 用户可在 UI 里编辑/添加)"""
        self._files = files
        self._output_dir = output_dir
        self._time_tokens = list(default_time_tokens)
        self._state = 'idle'   # idle / running / done
        self._failed_count = 0
        self._cells_processed = 0
        self._closed = False
        self._photo_refs: list[tk.PhotoImage] = []   # 阻止 PhotoImage GC
        # 标题列按像素截断用 (Label(width=N) 是最小字符数不是上限, 挡不住深路径撑宽)
        self._cell_font = tkfont.Font(font=('TkDefaultFont', 9))
        self._cancel_event = threading.Event()
        self._worker_thread = None
        self._result_queue: queue.Queue | None = None
        self._total = len(self._files) * len(self._time_tokens)

        self.top = tk.Toplevel(parent)
        self.top.title("定点截图")
        self.top.transient(parent)
        self.top.grab_set()
        self.top.geometry("1200x700")
        # 悬停显示被截断的标题全路径 (与主网格共用同一个 Tooltip 类)。
        # 必须放在 self.top 建好之后 —— Tooltip 拿它当 Toplevel 的 owner。
        self.tooltip = Tooltip(self.top)

        self._build_output_dir_row()
        self._build_time_points_row()
        self._build_table_initial()
        self._build_status_row()
        self.top.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- UI 构建 ----

    def _build_output_dir_row(self):
        """最顶一行: 截图地址 label + Entry + 「浏览」+ 「开始截图」按钮 (最右)"""
        row = ttk.Frame(self.top, padding=5)
        row.pack(side='top', fill='x')
        ttk.Label(row, text="截图地址:").pack(side='left', padx=(0, 5))
        self._output_dir_var = tk.StringVar(value=self._output_dir)
        self._output_dir_entry = ttk.Entry(row, textvariable=self._output_dir_var)
        self._output_dir_entry.pack(side='left', padx=(0, 5), fill='x', expand=True)
        ttk.Button(
            row, text="浏览", command=self._browse_output_dir,
        ).pack(side='left', padx=2)
        # 「开始截图」按钮 (与截图地址同一行, 最右; state 切换 idle/running/done)
        self._action_btn = ttk.Button(
            row, text="开始截图", width=14, command=self._on_action_btn,
        )
        self._action_btn.pack(side='right')

    def _browse_output_dir(self) -> None:
        """「浏览」按钮: 选截图存放的**父目录**, 下面套一层 snapshot。

        Entry 始终显示最终输出目录 (= 所选文件夹\\snapshot), 与默认值
        <文件路径>\\snapshot 是同一套约定。目录本身等点「开始截图」才建。
        """
        from tkinter import filedialog
        folder = filedialog.askdirectory(
            title="选择截图存放文件夹", parent=self.top,
        )
        if folder:
            if sys.platform == 'win32':
                folder = folder.replace('/', '\\')
            self._output_dir_var.set(os.path.join(folder, 'snapshot'))

    def _build_time_points_row(self):
        """一行: 时间点 label + N 个 Entry + 「+」按钮 (最后)"""
        row = ttk.Frame(self.top, padding=5)
        row.pack(side='top', fill='x')
        ttk.Label(row, text="时间点:").pack(side='left', padx=(0, 5))
        self._tp_row = ttk.Frame(row)
        self._tp_row.pack(side='left', fill='x', expand=True)
        self._time_token_vars: list[tk.StringVar] = []
        self._time_token_entries: list[ttk.Entry] = []
        # 先建 + 按钮 (作为「最后位置」标记, entries 排它前面)
        self._add_btn = ttk.Button(
            row, text="+", width=3,
            command=lambda: self._add_time_token_entry("00:00:00:00"),
        )
        # 再建 entries (pack after= 保持 + 一直在最后)
        for tok in self._time_tokens:
            self._add_time_token_entry(tok)
        self._add_btn.pack(side='left', padx=(5, 0))

    def _add_time_token_entry(self, default: str = "00:00:00:00") -> None:
        """新加 (或初始化时) 一个时间点 Entry; 用 pack(after=...) 让新 entry 跟在
        当前最后 entry 之后 (即 + 按钮之前), 保持 + 按钮永远在末位"""
        var = tk.StringVar(value=default)
        e = ttk.Entry(self._tp_row, textvariable=var, width=10)
        # after=self._time_token_entries[-1] 让新 entry 排在最后一个 entry 之后
        # (= + 按钮之前); entries 空时 after=None 走默认 (leftmost)
        after_widget = self._time_token_entries[-1] if self._time_token_entries else None
        e.pack(side='left', padx=(0, 5), after=after_widget)
        # 双击只选所在段 (HH/MM/SS/frame), 不全选
        e.bind('<Double-Button-1>', self._on_entry_double_click)
        self._time_token_vars.append(var)
        self._time_token_entries.append(e)

    def _on_entry_double_click(self, event) -> str:
        """双击 Entry: 按 x 坐标定位所在段, select_range 选该段, 阻止默认全选"""
        entry = event.widget
        try:
            idx = entry.index(f'@{event.x}')
        except tk.TclError:
            return 'break'
        text = entry.get()
        if not text:
            return 'break'
        colons = [i for i, c in enumerate(text) if c == ':']
        if not colons:
            entry.select_range(0, len(text))
            entry.icursor('end')
            return 'break'
        if idx <= colons[0]:
            sec_start, sec_end = 0, colons[0]
        elif len(colons) >= 2 and idx <= colons[1]:
            sec_start, sec_end = colons[0] + 1, colons[1]
        elif len(colons) >= 3 and idx <= colons[2]:
            sec_start, sec_end = colons[1] + 1, colons[2]
        else:
            sec_start, sec_end = colons[-1] + 1, len(text)
        entry.select_range(sec_start, sec_end)
        entry.icursor(sec_end)
        return 'break'

    def _build_table_initial(self):
        """初始表 (无时间点列): 只「标题」col, 文件名列. 时间点列在起跑时按选中 tokens 加."""
        self._middle = ttk.Frame(self.top)
        self.canvas = tk.Canvas(self._middle, highlightthickness=0)
        vsb = ttk.Scrollbar(self.top, orient='vertical', command=self.canvas.yview)
        # 横向滚动条: master 必须直接是 _middle (它被 pack 进的容器),
        # 不能 pack(in_=...) 跨容器 —— Windows 上那样 widget 会显示 0 尺寸。
        self._hsb = ttk.Scrollbar(self._middle, orient='horizontal',
                                  command=self.canvas.xview)
        self.canvas.config(yscrollcommand=vsb.set, xscrollcommand=self._hsb.set)
        self.canvas.pack(side='top', fill='both', expand=True)
        self._hsb.pack(side='bottom', fill='x')
        vsb.pack(side='right', fill='y')
        self._middle.pack(side='top', fill='both', expand=True)

        self._inner = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self._inner, anchor='nw')
        # inner frame 几何变化时更新 scrollregion (用 winfo_req*, 不用 bbox('all'))
        self._inner.bind('<Configure>', lambda _e: self.canvas.config(scrollregion=(
            0, 0, self._inner.winfo_reqwidth(), self._inner.winfo_reqheight())))
        # Mouse wheel: 见 _bind_wheel —— Tk 事件只沿 bindtags 派发, 不向父冒泡,
        # 父上绑了也不覆盖子 widget, 所以 canvas / _inner / 表头 / cell 各自都要绑。
        self._bind_wheel(self.canvas)
        self._bind_wheel(self._inner)

        # 列 0 = 序号 (固定宽), 列 1 = 标题 (250px), 无时间点 col
        self._inner.grid_columnconfigure(0, minsize=self.IDX_COL_WIDTH)
        self._inner.grid_columnconfigure(1, minsize=self.TITLE_COL_WIDTH)
        # 表头: 与主界面 VideoGridView 一致 — tk.Label / TkDefaultFont 9 (非粗) /
        # relief='raised' + bd=1 / padx=4 pady=2
        # 表头直接 grid 进 _inner 的 row 0 (数据行从 row 1 起), 不另起一个 frame:
        #  - 表头与数据行在同一个 scrollregion 里, 横向滚动时一起动。表头若是 canvas
        #    的兄弟 widget 就会钉在视口上, 横滚后「T1」指着 T3 的图。
        #  - 列宽由同一个 grid 分配, 表头列必然与数据列对齐。若把表头塞进另一个
        #    frame 再 columnspan 横跨, 它会按表头 label 自己的自然宽独立重跑一次列
        #    分配, 与数据 cell 那次的结果差出几像素。
        idx_hdr = tk.Label(self._inner, text="序号", font=('TkDefaultFont', 9),
                           relief='raised', bd=1, width=5, anchor='center')
        idx_hdr.grid(row=0, column=0, sticky='nsew', padx=4, pady=2)
        self._bind_wheel(idx_hdr)
        ttl_hdr = tk.Label(self._inner, text="标题", font=('TkDefaultFont', 9),
                           relief='raised', bd=1, width=28, anchor='w')
        # 标题列的 grid() 不加 padx (与数据行同一约定, 见 _make_row_labels 的注释)
        ttl_hdr.grid(row=0, column=1, sticky='nsew', pady=2)
        self._bind_wheel(ttl_hdr)
        # 表头 label 按列序留一份引用 (跟 _cells / _row_*_labels 同一套), 便于
        # 「表头第 k 列 ↔ 第 0 行第 k 个 cell」的对齐检查 (断言脚本用)
        self._header_labels: list[tk.Label] = [idx_hdr, ttl_hdr]

        # 行: 序号 + 文件名列 (无时间点 cell)
        self._cells: list[list[tk.Label]] = []
        self._row_title_labels: list[tk.Label] = []
        self._row_idx_labels: list[tk.Label] = []
        for r, info in enumerate(self._files):
            idx_lbl, title_lbl = self._make_row_labels(r, info)
            self._row_idx_labels.append(idx_lbl)
            self._row_title_labels.append(title_lbl)
            self._cells.append([])  # 空行 cells, 跟 n_cols=0 对应

    def _bind_wheel(self, w) -> None:
        """把纵向滚轮绑到 w 上。

        Tk 事件只沿 bindtags (自身 / class / toplevel / all) 派发, **不向父 widget
        冒泡** —— 绑在 canvas 或 _inner 上, 并不能覆盖停在它们子 widget 上的滚轮。
        所以每个要能滚的 widget 都得各自显式绑一遍。
        """
        w.bind('<MouseWheel>', self._on_mousewheel)
        w.bind('<Button-4>', self._on_mousewheel_linux)
        w.bind('<Button-5>', self._on_mousewheel_linux)

    def _make_row_labels(self, r: int, info) -> tuple[tk.Label, tk.Label]:
        """建一行的「序号」+「标题」两个 label.

        配色对齐主界面 VideoGridView._cell_fg 的语义 — 不画整行底色,
        只用 fg 区分: 达标全绿字, 不达标标题红字。

        标题显示完整相对路径 (与主界面 format_title 一致), 再按
        TITLE_COL_WIDTH 像素截断 —— 不截的话一个深路径会把整列撑宽,
        把所有时间点列推右。该列没设 weight, 列宽 = max(minsize, 所有
        label 的 reqwidth), Label(width=28) 是最小字符数, 挡不住。

        row 从 1 起: row 0 留给表头 label, 表头与数据行共用 _inner 的 grid。
        """
        if info.is_passing:
            fg = self.FG_PASS
        else:
            fg = self.FG_FAIL
        idx_lbl = tk.Label(self._inner, text=str(r + 1), anchor='center',
                           width=5, font=('TkDefaultFont', 9),
                           padx=4, pady=2, fg=fg)
        idx_lbl.grid(row=r + 1, column=0, sticky='nsew', padx=4, pady=2)
        self._bind_wheel(idx_lbl)
        full_title = format_title(info)
        shown_title = truncate_text(full_title, self.TITLE_COL_WIDTH,
                                    _font_measure(self._cell_font))
        title_lbl = tk.Label(self._inner, text=shown_title, anchor='w',
                             font=('TkDefaultFont', 9),
                             padx=4, pady=2, fg=fg)
        # _full_text / _truncated 供 tooltip 用 (见 Tooltip.on_motion)
        title_lbl._full_text = full_title
        title_lbl._truncated = (shown_title != full_title)
        title_lbl.bind('<Motion>', self.tooltip.on_motion)
        title_lbl.bind('<Leave>', self.tooltip.on_leave)
        # 标题列的 grid() 不加 padx: 左右留白全部由 Label 自己的 padx=4 承担
        # (与主网格 VideoGridView 一致 —— 那边 cell.grid() 同样不带 padx)。
        # truncate_text 的 col_width-10 余量就是按「Label padx 两边 8 + bd 两边 2」
        # 算的; 这里若再叠一层 grid padx, 列宽会超出 minsize(250),
        # 一个深路径就能把整列撑宽。表头那一列必须同步改, 否则表头与数据错开 4px。
        title_lbl.grid(row=r + 1, column=1, sticky='nsew', pady=2)
        self._bind_wheel(title_lbl)
        return idx_lbl, title_lbl

    def _update_table(self, new_tokens: list[str]) -> None:
        """起跑前调用: 按 new_tokens 重建表头 + cells.
        (清掉旧 photo refs; 销毁旧子 widget; 重建; 重设 column minsize.)"""
        # 下面会销毁 _inner 的所有子 widget, tooltip 若还开着, 它的 _cell
        # 就指向一个已销毁的 label —— 先关掉 (主网格 clear() 也是这么做的)。
        self.tooltip.hide()
        self._time_tokens = list(new_tokens)
        n_cols = len(new_tokens)
        cell_col_w = self.THUMB_DISPLAY_W + 8

        # 清掉旧子 widget + 旧 photo refs (避免内存泄漏)
        for w in self._inner.winfo_children():
            w.destroy()
        self._photo_refs.clear()

        # 重置所有 columnconfigure (清掉旧列的 minsize, 避免空列占位)
        for c in range(max(20, n_cols + 2)):
            self._inner.grid_columnconfigure(c, minsize=0)

        # 设置列宽: col0=序号, col1=标题, col2..=时间点
        self._inner.grid_columnconfigure(0, minsize=self.IDX_COL_WIDTH)
        self._inner.grid_columnconfigure(1, minsize=self.TITLE_COL_WIDTH)
        for c in range(n_cols):
            self._inner.grid_columnconfigure(c + 2, minsize=cell_col_w)

        # 表头: 序号 + 标题 + N 个时间点 (与数据行同一个 grid 的 row 0)
        # 表头: 与主界面 VideoGridView 一致 — tk.Label / TkDefaultFont 9 (非粗) /
        # relief='raised' + bd=1 / padx=4 pady=2
        self._header_labels = []
        idx_hdr = tk.Label(self._inner, text="序号", font=('TkDefaultFont', 9),
                           relief='raised', bd=1, width=5, anchor='center')
        idx_hdr.grid(row=0, column=0, sticky='nsew', padx=4, pady=2)
        self._bind_wheel(idx_hdr)
        ttl_hdr = tk.Label(self._inner, text="标题", font=('TkDefaultFont', 9),
                           relief='raised', bd=1, width=28, anchor='w')
        # 标题列的 grid() 不加 padx (与数据行同一约定, 见 _make_row_labels 的注释)
        ttl_hdr.grid(row=0, column=1, sticky='nsew', pady=2)
        self._bind_wheel(ttl_hdr)
        self._header_labels += [idx_hdr, ttl_hdr]
        for c, tok in enumerate(new_tokens):
            # padx=1 (不是 4): 与数据 cell 的 padx 一致, 表头框和缩略图框在列里
            # 左右边界完全重合 (差 3px 的话表头就指不到正对的那一列)。
            th_hdr = tk.Label(self._inner,
                              text=f"T{c + 1}: {time_token_to_string(tok)}",
                              font=('TkDefaultFont', 9),
                              relief='raised', bd=1, width=14)
            th_hdr.grid(row=0, column=c + 2, sticky='nsew', padx=1, pady=2)
            self._bind_wheel(th_hdr)
            self._header_labels.append(th_hdr)

        # body: 序号 + 标题 + N 个时间点 cell (初始 '…')
        self._cells = []
        self._row_title_labels = []
        self._row_idx_labels = []
        for r, info in enumerate(self._files):
            idx_lbl, title_lbl = self._make_row_labels(r, info)
            self._row_idx_labels.append(idx_lbl)
            self._row_title_labels.append(title_lbl)
            row_cells: list[tk.Label] = []
            for c in range(n_cols):
                # 图片 cell 无底色 (与主界面一致), 加载中用灰色「…」提示
                cell = tk.Label(self._inner, text='…', fg='gray',
                                padx=4, pady=2, cursor='hand2')
                cell.grid(row=r + 1, column=c + 2, sticky='nsew', padx=1, pady=1)
                cell.bind('<Button-1>', lambda _e, r=r, c=c: self._on_cell_click(r, c))
                # cell 占了弹窗绝大部分面积, 不绑滚轮的话鼠标停在图上滚轮不触发
                # (事件不向父 widget 冒泡, canvas/_inner 上的绑定覆盖不到 cell)
                self._bind_wheel(cell)
                cell._image_path = None
                row_cells.append(cell)
            self._cells.append(row_cells)

        # 强制 inner re-layout, 重设 scrollregion
        self._inner.update_idletasks()
        self.canvas.config(scrollregion=(
            0, 0, self._inner.winfo_reqwidth(), self._inner.winfo_reqheight()))
        self.canvas.yview_moveto(0)

    def _build_status_row(self):
        """底部: 状态文字 + 进度条 (从原版顶部挪下来)"""
        row = ttk.Frame(self.top, padding=5)
        row.pack(side='bottom', fill='x')
        self._status_var = tk.StringVar(value="就绪 — 点「开始截图」开始")
        ttk.Label(row, textvariable=self._status_var, anchor='w').pack(side='top', fill='x')
        self._progress = ttk.Progressbar(row, mode='determinate', length=400)
        self._progress.pack(side='top', fill='x', pady=(2, 0))
        self._progress['maximum'] = max(self._total, 1)
        self._progress['value'] = 0

    # ---- 状态机 + worker ----

    def _on_action_btn(self):
        """按钮点击: idle/done → 起新一次; running → 取消当前"""
        if self._state == 'running':
            self._cancel_event.set()
            self._action_btn.config(state='disabled')
            return
        self._start_snap()

    def _start_snap(self):
        """校验 → 锁 UI → 清旧 cell → 起 worker"""
        from tkinter import messagebox
        tokens = [v.get().strip() for v in self._time_token_vars]
        tokens = [t for t in tokens if t]
        if not tokens:
            messagebox.showwarning("提示", "请输入至少一个时间点。")
            return
        for tok in tokens:
            try:
                parse_time_token(tok, 30.0)  # fps 仅做语法校验
            except ValueError as e:
                messagebox.showerror("错误", f"时间点格式错误: {e}")
                return
        # 输出目录以「截图地址」Entry 为准 (可在弹窗内改 / 浏览)
        self._output_dir = self._output_dir_var.get().strip()
        if not self._output_dir:
            messagebox.showwarning("提示", "请先设置截图地址。")
            return
        try:
            os.makedirs(self._output_dir, exist_ok=True)
        except OSError as e:
            messagebox.showerror("错误", f"无法创建截图地址:\n{e}")
            return

        self._time_tokens = tokens
        # 按当前 tokens 重建表头 + cells (用户在跑之前可能改了时间点)
        self._update_table(tokens)

        self._cancel_event.clear()
        self._cells_processed = 0
        self._failed_count = 0
        self._total = len(self._files) * len(tokens)
        self._progress['maximum'] = max(self._total, 1)
        self._progress['value'] = 0
        self._state = 'running'
        self._action_btn.config(text="截图中", state='normal')
        self._set_time_points_state('disabled')
        self._output_dir_entry.config(state='disabled')

        self._result_queue = queue.Queue()
        self._worker_thread = threading.Thread(
            target=self._worker_entry,
            args=(tokens, self._output_dir),
            daemon=True,
        )
        self._worker_thread.start()
        self._poll_queue()

    def _worker_entry(self, time_tokens: list[str], output_dir: str):
        """后台 worker: 对每个文件 × 时间点顺序调 ffmpeg 抽帧"""
        files = self._files
        n_files = len(files)
        n_times = len(time_tokens)
        total = n_files * n_times
        done = 0
        for r, info in enumerate(files):
            if self._cancel_event.is_set():
                break
            for c, token in enumerate(time_tokens):
                if self._cancel_event.is_set():
                    break
                self._result_queue.put(('cell_start', (r, c)))
                self._result_queue.put(('progress', (done, total, info.title)))
                try:
                    seconds = parse_time_token(token, info.frame_rate_fps)
                except ValueError:
                    self._result_queue.put(('cell_failed', (r, c)))
                    done += 1
                    continue
                base = os.path.splitext(info.title)[0] or 'video'
                out_name = f"{base}_t{c + 1}_{time_token_to_string(token)}.png"
                out_path = os.path.join(output_dir, out_name)
                ok = extract_frame(info.full_path, seconds, out_path)
                if ok:
                    self._result_queue.put(('cell_done', (r, c, out_path)))
                else:
                    self._result_queue.put(('cell_failed', (r, c)))
                done += 1
        self._result_queue.put(('done', (done, total, self._cancel_event.is_set())))

    def _poll_queue(self):
        """主线程 100ms 轮询: 消费 cell 状态/进度消息"""
        try:
            while True:
                msg_type, payload = self._result_queue.get_nowait()
                if self._closed:
                    return
                if msg_type == 'cell_start':
                    r, c = payload
                    self.mark_loading(r, c)
                elif msg_type == 'cell_done':
                    r, c, path = payload
                    self.mark_done(r, c, path)
                elif msg_type == 'cell_failed':
                    r, c = payload
                    self.mark_failed(r, c)
                elif msg_type == 'progress':
                    done, total, current = payload
                    self.update_status(done, total, current)
                elif msg_type == 'done':
                    done, total, cancelled = payload
                    self._on_snap_done(done, total, cancelled)
                    return
        except queue.Empty:
            pass
        if not self._closed:
            self.top.after(100, self._poll_queue)

    def _on_snap_done(self, done: int, total: int, cancelled: bool):
        """worker 报告 done: 解锁 UI, 改按钮文字"""
        self._state = 'done' if not cancelled else 'idle'  # 取消 → 回 idle 允许重抽
        self._action_btn.config(text="开始截图", state='normal')
        self._set_time_points_state('normal')
        self._output_dir_entry.config(state='normal')
        status = "抽帧已取消" if cancelled else "抽帧完成"
        self._status_var.set(
            f"{status}: {done}/{total} ({done - self._failed_count} 成功, {self._failed_count} 失败)"
        )
        self._progress['value'] = self._progress['maximum']

    def _set_time_points_state(self, state: str):
        """锁/解锁所有时间点 Entry"""
        for e in self._time_token_entries:
            e.config(state=state)

    # ---- cell 状态 ----

    def mark_loading(self, r: int, c: int) -> None:
        cell = self._cells[r][c]
        cell.config(text='…', fg='blue', image='', compound='none')

    def mark_done(self, r: int, c: int, image_path: str) -> None:
        cell = self._cells[r][c]
        cell._image_path = image_path
        try:
            full = tk.PhotoImage(file=image_path)
            display = full.subsample(self.THUMB_SUBSAMPLE, self.THUMB_SUBSAMPLE)
            # 只留 display: full 仅用于这一次 subsample, EnlargeDialog 是从磁盘
            # 路径重新加载的。subsample 返回独立副本, full 出作用域即可回收 ——
            # 16:9 源每 cell 省约 0.9MB, 400 cell 场景约 360MB (抽帧宽度固定
            # 640px, 高度随源比例变; 4:3/竖版等其它比例的省量按原图尺寸增减)。
            self._photo_refs.append(display)
            # compound='center' 必填 (tk.Label 只认 none/center/left/right/top/bottom)
            cell.config(image=display, text='', compound='center', fg='black')
        except (tk.TclError, OSError):
            self.mark_failed(r, c)
            return
        self._cells_processed += 1
        self._progress['value'] = self._cells_processed

    def mark_failed(self, r: int, c: int) -> None:
        cell = self._cells[r][c]
        cell._image_path = None
        cell.config(text='×', fg='red', font=('TkDefaultFont', 14, 'bold'),
                   image='', compound='none')
        self._failed_count += 1
        self._cells_processed += 1
        self._progress['value'] = self._cells_processed

    def update_status(self, done: int, total: int, current: str) -> None:
        if self._state == 'idle':
            return  # 起跑前 worker 可能递进来 progress, 忽略
        suffix = f" ({current})" if current else ""
        self._status_var.set(f"抽帧中 {done}/{total}{suffix}")

    def _on_cell_click(self, r: int, c: int) -> None:
        """点 cell 弹放大图. 任何状态都可 (running 也能看已完成那些)"""
        cell = self._cells[r][c]
        if not cell._image_path:
            return
        info = self._files[r]
        EnlargeDialog(self.top, image_path=cell._image_path,
                      title=f"{info.title} @ T{c + 1}")

    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

    def _on_mousewheel_linux(self, event) -> None:
        delta = -1 if event.num == 4 else 1
        self.canvas.yview_scroll(delta, 'units')

    def _on_close(self) -> None:
        if self._state == 'running':
            from tkinter import messagebox
            ok = messagebox.askyesno("确认关闭", "抽帧还在进行中, 确定要关闭吗?",
                                     parent=self.top)
            if not ok:
                return
            self._cancel_event.set()
        self._closed = True
        self._photo_refs.clear()
        self.top.destroy()


class VideoCheckerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("视频检测v3.3")
        self.root.geometry("1280x700")
        self.root.minsize(1000, 500)

        self.video_results: list[VideoInfo] = []
        self.scanning = False
        self.cancel_event = threading.Event()
        self.total_files: int = 0
        self.result_queue: queue.Queue = queue.Queue()
        # 浏览「选文件」时记下具体文件列表 (None = 扫整个目录; list = 只扫这些)
        self._selected_files: list[str] | None = None
        # 达标数增量维护 (避免每次插入都全量 sum, O(N²) in N=1000 时无谓)
        self._passed_count: int = 0

        self._create_widgets()

    def _create_widgets(self):
        # === Row 1: 路径输入行 ===
        top_frame = ttk.Frame(self.root, padding=5)
        top_frame.pack(fill='x')

        ttk.Label(top_frame, text="文件路径:").pack(side='left')
        self.path_var = tk.StringVar()
        # 用户手动改路径框 → 丢弃「拖拽留下的选文件列表」模式。
        # 以前必须先点「清除记录」才生效, 现在手改路径就直接生效。
        self._suppress_path_trace = False
        self.path_var.trace_add('write', self._on_path_edited)
        self.path_entry = ttk.Entry(top_frame, textvariable=self.path_var, width=50)
        self.path_entry.pack(side='left', padx=(5, 2), fill='x', expand=True)

        ttk.Button(top_frame, text="浏览", command=self._browse_path).pack(side='left', padx=2)

        self.recursive_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top_frame, text="子文件夹", variable=self.recursive_var
        ).pack(side='left', padx=(10, 2))

        self.scan_btn = ttk.Button(top_frame, text="开始检测", command=self._on_scan_btn_click)
        self.scan_btn.pack(side='left', padx=(10, 0))

        # === Row 2: 码率标准 + 采样率标准 ===
        std_frame = ttk.Frame(self.root, padding=5)
        std_frame.pack(fill='x')

        ttk.Label(std_frame, text="码率标准:").pack(side='left')
        self.bitrate_var = tk.StringVar(value=str(DEFAULT_BITRATE_KBPS))
        self.bitrate_entry = ttk.Entry(std_frame, textvariable=self.bitrate_var, width=10)
        self.bitrate_entry.pack(side='left', padx=(5, 2))
        ttk.Label(std_frame, text="kbps").pack(side='left')

        ttk.Label(std_frame, text="采样率标准:").pack(side='left', padx=(15, 0))
        self.sample_rate_var = tk.StringVar(value=str(DEFAULT_SAMPLE_RATE_KHZ))
        self.sample_rate_entry = ttk.Entry(std_frame, textvariable=self.sample_rate_var, width=10)
        self.sample_rate_entry.pack(side='left', padx=(5, 2))
        ttk.Label(std_frame, text="kHz").pack(side='left')
        # 「定点截图」按钮放在采样率单位 kHz 后面, 与其他 left-pack 控件一行
        ttk.Button(std_frame, text="定点截图", command=self._on_snapshot_btn_click).pack(
            side='left', padx=(10, 0)
        )

        # 「清除记录」放在 std_frame 行右端, 通过右侧 padx 把按钮向左推 ~272 px,
        # 使之视觉上对齐 Row 1 的「文件路径」输入框右边缘（再往前一个身位,
        # 比原先对齐「浏览」的位置更靠左）。
        # pack 顺序: 所有 side='left' 控件都已在上面调用完毕, 这一行必须是最后。
        ttk.Button(std_frame, text="清除记录", command=self._clear_records).pack(
            side='right', padx=(0, 272)
        )

        # === Main: 结果表格（自绘 VideoGridView） ===
        tree_frame = ttk.Frame(self.root)
        tree_frame.pack(fill='both', expand=True, padx=5, pady=5)

        # 注意: 原来这里还有一个 columns 元组, 赋值后从未被读取 (死代码) —
        # VideoGridView 只收 headers。已删除, 避免改它的人以为那是真理来源。
        headers = {
            'title': ('标题', 250),
            'resolution': ('分辨率', 90),
            'frame_rate': ('帧率(fps)', 75),
            'bitrate': ('码率(kbps)', 85),
            'spec': ('规格', 140),
            'audio_channels': ('声道数', 55),
            'audio_sample_rate': ('采样率(kHz)', 90),
            'duration': ('时长', 75),
            'file_size': ('文件大小', 75),
            'result': ('结果', 60),
        }

        self.grid = VideoGridView(tree_frame, headers)
        self.grid.pack(fill='both', expand=True)

        # 全局快捷键
        self.root.bind('<Control-o>', lambda _e: self._browse_path())

        # === 状态栏: 一行文字 ===
        # 终态时后面拼上统计 (见 _set_status)。
        # 之前是两个 Label —— 左边「检测完成」右边「共 N · 达标 M · 不达标 X」,
        # 读起来像两件不相干的事, 合并成一行更好读。
        self.status_var = tk.StringVar(value="就绪")
        status_frame = ttk.Frame(self.root, relief='sunken', padding=3)
        status_frame.pack(fill='x', padx=5)
        ttk.Label(status_frame, textvariable=self.status_var, anchor='w').pack(
            side='left', fill='x', expand=True)

        # === Bottom: 迁移地址 + 移动/截图按钮 ===
        bottom_frame = ttk.Frame(self.root, padding=5)
        bottom_frame.pack(fill='x')

        dest_row = ttk.Frame(bottom_frame)
        dest_row.pack(fill='x')
        ttk.Label(dest_row, text="迁移地址:").pack(side='left')
        self.dest_var = tk.StringVar()
        self.dest_entry = ttk.Entry(dest_row, textvariable=self.dest_var, width=50)
        self.dest_entry.pack(side='left', padx=(5, 2), fill='x', expand=True)
        ttk.Button(dest_row, text="浏览", command=self._browse_dest).pack(side='left', padx=2)
        self.move_btn = ttk.Button(
            dest_row, text="移动达标文件", command=self._move_passing_files,
        )
        self.move_btn.pack(side='left', padx=(10, 0))
        self.move_all_btn = ttk.Button(
            dest_row, text="移动全部文件", command=self._move_all_files,
        )
        self.move_all_btn.pack(side='left', padx=(5, 0))

        # 截图输出目录 (弹窗内可改; 这里只存默认值, 由 _start_scan 自动填 <dir>/snapshot)
        self.snapshot_dest_var = tk.StringVar()

        # 启用拖拽文件到表格
        self.grid.bind_drop(self._on_file_drop)

    # ---- 事件处理 ----

    def _browse_path(self):
        """浏览源: 只选文件夹。

        原来先弹文件选择器、取消后再弹文件夹选择器 (fallback 两段弹)。
        已取消 —— 只保留文件夹, 一次一个对话框。选单个/多个视频文件的场景
        走拖拽 (bind_drop → _on_file_drop), 不再从「浏览」进。
        """
        folder = filedialog.askdirectory(title="选择视频文件夹")
        if folder:
            self._selected_files = None
            self._set_path(folder)
            self._sync_derived_paths(folder)

    def _set_path(self, path: str) -> None:
        """程序自己写「文件路径」框 (浏览 / 拖拽), 不触发「用户改过路径」的副作用。"""
        self._suppress_path_trace = True
        try:
            self.path_var.set(path)
        finally:
            self._suppress_path_trace = False

    def _on_path_edited(self, *_args) -> None:
        """用户手动改了「文件路径」→ 丢掉拖拽留下的选文件列表。

        拖拽会把文件记进 _selected_files, 之后 _start_scan 扫的是那批文件
        而不是路径框里的目录 (见 _scan_target)。以前要等用户点「清除记录」
        才切得回去 —— 现在手改路径框就立即生效, 不用先清除记录。

        浏览选文件夹本来就是这个行为 (它也把 _selected_files 清了), 一致。
        """
        if self._suppress_path_trace:
            return
        self._selected_files = None

    def _sync_derived_paths(self, directory: str) -> None:
        """迁移地址 + 截图地址跟随源目录, 无条件覆盖。

        这两个字段是**从「文件路径」派生**的, 不是用户自己配的 —— 源目录
        一变就该重算。旧的写法是 `if not dest_var.get().strip()` 才填,
        那个 guard 区分不了「从没填过」和「上次为别的目录填过」: 第一次
        扫 A 填成 A\\checked, 之后把文件路径改成 B 再开始检测, 字段非空 →
        不重算 → 迁移和截图仍然指向 A。

        调用点三处, 都要走这里, 别再各自拼一遍:
          - `_browse_path`  选了文件夹, 立刻给反馈 (还没点开始检测)
          - `_start_scan`   手动改过「文件路径」后开始检测
          - `_on_file_drop` 拖进来的文件所在目录

        **只填字段, 不建目录** —— 目录等真要用的时候再建:
          - snapshot → 弹窗点「开始截图」时 (SnapshotProgressDialog._start_snap)
          - checked  → 点「移动达标/全部文件」时 (_move_passing_files)
        扫描时就在源目录里建 snapshot, 会给不需要它的人留一个空文件夹。
        """
        # 迁移地址: 无条件转反斜杠 (Windows 观感统一, 与既有行为一致)
        self.dest_var.set(
            os.path.join(directory, "checked").replace('/', '\\'))
        # 截图地址: 只在 win32 转反斜杠 —— macOS 上反斜杠路径 os.path.isdir
        # 不认, 建出来的目录会等于没建
        snap_default = os.path.join(directory, 'snapshot')
        if sys.platform == 'win32':
            snap_default = snap_default.replace('/', '\\')
        self.snapshot_dest_var.set(snap_default)

    def _browse_dest(self):
        """选迁移地址: 所选文件夹当**父目录**, 下面套一层 checked。

        Entry 始终显示最终目标目录 (= 所选文件夹\\checked), 与默认值
        <文件路径>\\checked 是同一套约定。目录本身等点移动时才建。
        """
        folder = filedialog.askdirectory(title="选择迁移地址")
        if folder:
            self.dest_var.set(
                os.path.join(folder, "checked").replace('/', '\\'))

    def _on_scan_btn_click(self):
        """扫描按钮点击: 空闲时启动检测, 检测中触发停止。"""
        if self.scanning:
            self.cancel_event.set()
            self.scan_btn.config(state='disabled', text='正在停止...')
        else:
            self._start_scan()

    def _start_scan(self):
        directory = self.path_var.get().strip()
        if not directory or not os.path.isdir(directory):
            messagebox.showerror("错误", "请输入有效的文件夹路径。")
            return

        bitrate_std = parse_threshold(self.bitrate_var.get())
        if bitrate_std is None:
            messagebox.showerror("错误", "码率标准必须为正数。")
            return

        sample_rate_std = parse_threshold(self.sample_rate_var.get())
        if sample_rate_std is None:
            messagebox.showerror("错误", "采样率标准必须为正数。")
            return

        # 迁移地址 + 截图地址跟随本次扫描的源目录 (无条件覆盖, 见
        # _sync_derived_paths 的注释 —— 旧的「空才填」会留在上一个目录上)
        self._sync_derived_paths(directory)

        self.cancel_event.clear()
        self.scanning = True
        self.total_files = 0
        self.scan_btn.config(text='检测中')
        self.video_results.clear()
        # 清空表格
        self.grid.clear()
        # 新一轮扫描: 计数从头来 (漏了这一步 _passed_count 会带着上一轮的
        # 达标数累加, 状态栏出现「达标 188 · 不达标 -178」这种数)
        self._passed_count = 0

        self.result_queue = queue.Queue()

        # 扫拖入的那批文件, 还是扫路径框里的整个目录
        kind, payload = self._scan_target(directory)
        if kind == 'files':
            t = threading.Thread(
                target=self._scan_files_worker,
                args=(payload, bitrate_std, sample_rate_std),
                daemon=True,
            )
        else:
            t = threading.Thread(
                target=self._scan_worker,
                args=(payload, self.recursive_var.get(), bitrate_std, sample_rate_std),
                daemon=True,
            )
        t.start()
        self._check_queue()

    def _scan_target(self, directory: str) -> tuple[str, object]:
        """本次扫描的目标: ('files', 文件列表) 或 ('dir', 目录)。

        拖拽加载的文件优先 —— 但只在用户没手动改过「文件路径」时。
        用户手改了路径框, _on_path_edited 就把 _selected_files 丢了,
        于是这里回落到「扫路径框里的整个目录」。不需要先点「清除记录」。
        """
        if self._selected_files:
            return ('files', self._selected_files)
        return ('dir', directory)

    def _scan_worker(self, directory: str, recursive: bool, bitrate_std: float, sample_rate_std: float):
        """扫描工作线程"""
        self.result_queue.put(('status', '正在扫描文件列表...'))
        files = scan_video_files(directory, recursive)
        self.total_files = len(files)
        total = len(files)

        if total == 0:
            self.result_queue.put(('status', '未找到视频文件'))
            self.result_queue.put(('done', None))
            return

        for i, fp in enumerate(files, 1):
            if self.cancel_event.is_set():
                break
            self.result_queue.put(('status', f'正在检测... ({i}/{total}) {os.path.basename(fp)}'))
            data = run_ffprobe(fp)
            if data is None:
                continue
            info = parse_video_info(data, fp, directory, bitrate_std, sample_rate_std)
            if info is not None:
                self.result_queue.put(('result', info))

        self.result_queue.put(('done', None))

    def _check_queue(self):
        """主线程定期检查结果队列"""
        try:
            while True:
                msg_type, payload = self.result_queue.get_nowait()
                if msg_type == 'result':
                    self._add_result(payload)
                elif msg_type == 'status':
                    self._set_status(payload)
                elif msg_type == 'done':
                    self._scan_complete()
                    return
        except queue.Empty:
            pass

        if self.scanning:
            self.root.after(100, self._check_queue)

    def _add_result(self, info: VideoInfo):
        """添加一条结果到表格"""
        self.video_results.append(info)
        if info.is_passing:
            self._passed_count += 1
        self.grid.add_row(info)

    def _set_status(self, text: str, with_counts: bool = False) -> None:
        """状态栏那一行字。

        终态 (完成/停止/清除) 带上统计, 中间状态 (就绪/扫描中) 只有进度文字
        —— 扫描中那条本来就带 (3/10) 的进度, 再挂一串统计反而看不清。

        统计里的「共 N」指**已出结果**的文件数; 停止时的 N/M 里的 M 是找到的
        文件总数, 两个数来源不同。
        """
        if with_counts:
            total = len(self.video_results)
            passed = self._passed_count
            text = f"{text}：共 {total} · 达标 {passed} · 不达标 {total - passed}"
        self.status_var.set(text)

    def _scan_complete(self):
        """扫描完成处理"""
        cancelled = self.cancel_event.is_set()
        self.scanning = False
        self.scan_btn.config(state='normal', text='开始检测')

        # 扫描中途按插入序追加结果; 若已激活排序, 扫描结束统一 reorder 一次
        if self.grid.is_sorted():
            self.grid.reorder()

        completed = len(self.video_results)
        passed = sum(1 for v in self.video_results if v.is_passing)
        self._passed_count = passed
        if cancelled:
            queued = self.total_files
            # 只留一个冒号: 原来这条自带计数 ("共 100 个视频文件, 已完成 80 个
            # 文件, 20 个未检测"), 再拼统计就成两个冒号 + 两次「共」
            self._set_status(
                f"已停止检测（已完成 {completed}/{queued}）", with_counts=True)
        else:
            # 目录里一个视频都没有时, 说「检测完成：共 0 · 达标 0 · 不达标 0」
            # 是废话 —— 统计全是 0, 用户要知道的其实是「没找到文件」。
            # (原来 worker 发过一条「未找到视频文件」, 但紧接着的 done 把它覆盖了,
            #  用户永远看不到。)
            if self.total_files == 0:
                self._set_status("未找到视频文件")
            else:
                self._set_status("检测完成", with_counts=True)

    def _clear_records(self):
        """清空检测记录与目标路径（不影响阈值输入）。"""
        if self.scanning:
            messagebox.showinfo("提示", "正在检测中，请等待完成后再清除。")
            return
        self.video_results.clear()
        self.grid.clear()
        self._passed_count = 0
        self.dest_var.set('')
        # 截图地址一起清: 两个字段同源, 只清一个会让「清除记录」后截图地址
        # 还指着一个已经跟当前列表无关的目录
        self.snapshot_dest_var.set('')
        self._selected_files = None
        self._set_status("已清除记录", with_counts=True)

    def _on_snapshot_btn_click(self):
        """「定点截图」按钮: 直接开 SnapshotProgressDialog (单窗口, 内嵌时间点输入)"""
        if self.scanning:
            messagebox.showinfo("提示", "正在检测中, 请等待完成后再截图。")
            return
        if not self.video_results:
            messagebox.showinfo("提示", "请先扫描视频。")
            return
        # 输出目录: 主窗口「截图地址」字段, 已被 _start_scan 自动填过 <dir>/snapshot
        output_dir = self.snapshot_dest_var.get().strip()
        if not output_dir:
            # 兜底 (用户从未触发 scan): 用 path_var/snapshot
            parent = self.path_var.get().strip()
            if parent and os.path.isdir(parent):
                output_dir = os.path.join(parent, 'snapshot')
                if sys.platform == 'win32':
                    output_dir = output_dir.replace('/', '\\')
        SnapshotProgressDialog(self.root, files=self.video_results, output_dir=output_dir)
        # 不 wait_window — 弹窗自带按钮 (开始/截图中) 与关闭 X 按钮, 不需要返回 result

    def _move_passing_files(self):
        """移动达标文件到目标目录"""
        passing = [v for v in self.video_results if v.is_passing]
        if not passing:
            messagebox.showinfo("提示", "没有达标文件需要移动。")
            return

        dest_dir = self.dest_var.get().strip()
        if not dest_dir:
            messagebox.showerror("错误", "请输入目标文件夹路径。")
            return

        confirm = messagebox.askyesno(
            "确认移动",
            f"确定要将 {len(passing)} 个达标文件移动到:\n{dest_dir}\n？"
        )
        if not confirm:
            return

        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as e:
            messagebox.showerror("错误", f"无法创建目标目录:\n{e}")
            return

        moved = 0
        failed_list = []
        for info in passing:
            try:
                dest_path = self._get_unique_dest(info.full_path, dest_dir)
                shutil.move(info.full_path, dest_path)
                moved += 1
            except (OSError, shutil.Error) as e:
                failed_list.append(f"{info.rel_path}: {e}")

        msg = f"成功移动 {moved} 个文件。"
        if failed_list:
            msg += f"\n\n{len(failed_list)} 个文件移动失败:\n" + "\n".join(failed_list[:20])
        messagebox.showinfo("移动结果", msg)

    def _move_all_files(self):
        """移动全部文件到目标目录"""
        files_to_move = self.video_results
        if not files_to_move:
            messagebox.showinfo("提示", "没有文件需要移动。")
            return

        dest_dir = self.dest_var.get().strip()
        if not dest_dir:
            messagebox.showerror("错误", "请输入目标文件夹路径。")
            return

        confirm = messagebox.askyesno(
            "确认移动",
            f"确定要将 {len(files_to_move)} 个文件移动到:\n{dest_dir}\n？"
        )
        if not confirm:
            return

        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as e:
            messagebox.showerror("错误", f"无法创建目标目录:\n{e}")
            return

        moved = 0
        failed_list = []
        for info in files_to_move:
            try:
                dest_path = self._get_unique_dest(info.full_path, dest_dir)
                shutil.move(info.full_path, dest_path)
                moved += 1
            except (OSError, shutil.Error) as e:
                failed_list.append(f"{info.rel_path}: {e}")

        msg = f"成功移动 {moved} 个文件。"
        if failed_list:
            msg += f"\n\n{len(failed_list)} 个文件移动失败:\n" + "\n".join(failed_list[:20])
        messagebox.showinfo("移动结果", msg)

    @staticmethod
    def _get_unique_dest(src_path: str, dest_dir: str) -> str:
        """获取不重复的目标路径"""
        filename = os.path.basename(src_path)
        dest = os.path.join(dest_dir, filename)
        if not os.path.exists(dest):
            return dest

        name, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(dest):
            dest = os.path.join(dest_dir, f"{name}_{counter}{ext}")
            counter += 1
        return dest

    def _on_file_drop(self, event):
        """处理拖拽到窗口的文件"""
        if self.scanning:
            messagebox.showinfo("提示", "正在检测中，请等待完成后再添加文件。")
            return
        bitrate_std = parse_threshold(self.bitrate_var.get())
        if bitrate_std is None:
            messagebox.showerror("错误", "码率标准必须为正数。")
            return

        sample_rate_std = parse_threshold(self.sample_rate_var.get())
        if sample_rate_std is None:
            messagebox.showerror("错误", "采样率标准必须为正数。")
            return

        # 解析拖拽的文件列表: 支持 Windows ``{file1} {file2}`` 与 macOS / Linux 空格分隔
        file_list = _parse_drop_data(event.data)
        if not file_list:
            return

        video_files = [f for f in file_list if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS]
        if not video_files:
            messagebox.showinfo("提示", "没有找到视频文件。")
            return

        # 在文件路径输入框显示第一个被拖入的文件所在目录（不含文件名）,
        # 迁移地址 + 截图地址跟随该目录 (原来这里只填了迁移地址, 截图地址
        # 留在上一次扫描的目录上)。
        # 用 _set_path: 拖拽是程序自己填路径框, 不能触发「用户改过路径 →
        # 丢弃文件列表」那条 (见 _on_path_edited)。
        self._set_path(os.path.dirname(video_files[0]).replace('/', '\\'))
        self._sync_derived_paths(os.path.dirname(video_files[0]))
        # 记下拖入的具体文件 —— 「浏览」已只保留文件夹, 拖拽是唯一的选文件入口
        self._selected_files = video_files

        self.cancel_event.clear()
        self.scanning = True
        self.scan_btn.config(text='检测中')
        self.video_results.clear()
        self.grid.clear()
        # 同 _start_scan: 新一轮扫描计数归零
        self._passed_count = 0

        self.result_queue = queue.Queue()

        t = threading.Thread(
            target=self._scan_files_worker,
            args=(video_files, bitrate_std, sample_rate_std),
            daemon=True,
        )
        t.start()
        self._check_queue()

    def _scan_files_worker(self, files: list[str], bitrate_std: float, sample_rate_std: float):
        """扫描拖拽的文件"""
        self.total_files = len(files)
        self.result_queue.put(('status', f'正在检测 {len(files)} 个文件...'))
        base_path = os.path.dirname(files[0]) if files else '.'

        for i, fp in enumerate(files, 1):
            if self.cancel_event.is_set():
                break
            self.result_queue.put(('status', f'正在检测... ({i}/{len(files)}) {os.path.basename(fp)}'))
            data = run_ffprobe(fp)
            if data is None:
                continue
            info = parse_video_info(data, fp, base_path, bitrate_std, sample_rate_std)
            if info is not None:
                self.result_queue.put(('result', info))

        self.result_queue.put(('done', None))


def main():
    root = TkinterDnD.Tk()
    VideoCheckerApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
