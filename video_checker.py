"""视频码率检查器 - 检测视频文件码率是否达标"""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from functools import lru_cache
import tkinter as tk
from dataclasses import dataclass
from tkinter import filedialog, font as tkfont, messagebox, ttk
from tkinterdnd2 import TkinterDnD

# 视频扩展名集合
VIDEO_EXTENSIONS = {
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.ts', '.m2ts',
    '.mpg', '.mpeg', '.webm', '.3gp', '.m4v', '.vob', '.ogv',
    '.rm', '.rmvb', '.asf', '.divx', '.f4v', '.mxf', '.mts',
    '.m1v', '.m2v', '.mpv', '.qt', '.y4m', '.nut', '.dv',
    '.tp', '.trp', '.hevc', '.h264', '.h265', '.264', '.265',
}

DEFAULT_BITRATE_KBPS = 30000
DEFAULT_SAMPLE_RATE_KHZ = 48


@lru_cache(maxsize=1)
def get_ffprobe_path() -> str:
    """获取 ffprobe 路径（优先使用打包的，fallback 到 PATH）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包环境，sys._MEIPASS 是内部属性
        base_dir = sys._MEIPASS
        local_ffprobe = os.path.join(base_dir, 'ffprobe', 'ffprobe.exe')
        if os.path.exists(local_ffprobe):
            return local_ffprobe
    # 回退到 PATH（shutil.which 返回 None 若不存在）
    return shutil.which('ffprobe') or 'ffprobe'


@dataclass
class VideoInfo:
    rel_path: str
    title: str
    resolution: str
    frame_rate: str
    bitrate_kbps: float
    video_codec: str
    audio_codec: str
    audio_channels: str
    audio_sample_rate: str
    duration: str
    file_size: str
    is_passing: bool
    sample_rate_passing: bool   # NEW: 采样率是否达标，用于按列染色
    bitrate_std: float          # NEW: 码率阈值（kbps），扫描时写入
    sample_rate_std: float      # NEW: 采样率阈值（kHz），扫描时写入
    full_path: str


def format_duration(seconds: float) -> str:
    """将秒数格式化为 HH:MM:SS"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_file_size(size_bytes: int) -> str:
    """将字节数格式化为人类可读大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    else:
        return f"{size_bytes / 1024 ** 3:.2f} GB"


def run_ffprobe(file_path: str) -> dict | None:
    """调用 ffprobe 获取视频文件信息"""
    try:
        ffprobe_path = get_ffprobe_path()
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE

        result = subprocess.run(
            [
                ffprobe_path, '-v', 'quiet',
                '-print_format', 'json',
                '-show_format', '-show_streams',
                file_path,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout.decode('utf-8', errors='replace'))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def parse_video_info(
    data: dict, full_path: str, base_path: str,
    bitrate_std: float, sample_rate_std: float,
) -> VideoInfo | None:
    """从 ffprobe JSON 数据解析视频信息"""
    streams = data.get('streams', [])
    fmt = data.get('format', {})

    # 查找视频流和音频流
    video_stream = None
    audio_stream = None
    for s in streams:
        if s.get('codec_type') == 'video' and video_stream is None:
            video_stream = s
        elif s.get('codec_type') == 'audio' and audio_stream is None:
            audio_stream = s

    if video_stream is None:
        return None  # 没有视频流，跳过

    # 分辨率
    width = video_stream.get('width', 0)
    height = video_stream.get('height', 0)
    resolution = f"{width}x{height}" if width and height else "N/A"

    # 帧率
    r_frame_rate = video_stream.get('r_frame_rate', '0/1')
    try:
        num, den = r_frame_rate.split('/')
        fps = float(num) / float(den) if float(den) != 0 else 0
        frame_rate = f"{fps:.2f} fps"
    except (ValueError, ZeroDivisionError):
        frame_rate = "N/A"

    # 码率 (bps -> kbps)
    # 优先使用视频流码率，其次使用 format 码率
    bitrate_bps = video_stream.get('bit_rate')
    if not bitrate_bps:
        bitrate_bps = fmt.get('bit_rate')
    try:
        bitrate_kbps = float(bitrate_bps) / 1000.0
    except (TypeError, ValueError):
        bitrate_kbps = 0.0

    # 视频编码
    video_codec = video_stream.get('codec_name', 'N/A')

    # 音频信息
    audio_codec = audio_stream.get('codec_name', 'N/A') if audio_stream else 'N/A'
    audio_channels = str(audio_stream.get('channels', 'N/A')) if audio_stream else 'N/A'

    # 音频采样率 (Hz -> kHz)
    if audio_stream is not None:
        sample_rate_raw = audio_stream.get('sample_rate')
        try:
            sample_rate_hz = float(sample_rate_raw)
            audio_sample_rate = f"{sample_rate_hz / 1000:.1f} kHz"
            sample_rate_passing = (sample_rate_hz / 1000.0) >= sample_rate_std
        except (TypeError, ValueError):
            audio_sample_rate = "N/A"
            sample_rate_passing = False
    else:
        audio_sample_rate = "N/A"
        sample_rate_passing = True  # 无音频流不算不达标

    # 时长
    duration_sec = fmt.get('duration')
    try:
        duration = format_duration(float(duration_sec))
    except (TypeError, ValueError):
        duration = "N/A"

    # 文件大小
    size_str = fmt.get('size')
    try:
        file_size = format_file_size(int(size_str))
    except (TypeError, ValueError):
        try:
            file_size = format_file_size(os.path.getsize(full_path))
        except OSError:
            file_size = "N/A"

    # 标题
    tags = fmt.get('tags', {})
    # tags 的 key 可能大小写不一致
    title = None
    for k, v in tags.items():
        if k.lower() == 'title':
            title = v
            break
    if not title:
        title = os.path.splitext(os.path.basename(full_path))[0]

    # 相对路径：当前文件夹显示"./"，子文件夹显示"./A"、"./A/B"
    try:
        rel_dir = os.path.relpath(os.path.dirname(full_path), base_path)
        if rel_dir == '.':
            rel_path = './'
        else:
            rel_path = './' + rel_dir.replace('\\', '/')
    except ValueError:
        rel_path = full_path

    is_passing = (bitrate_kbps >= bitrate_std) and sample_rate_passing

    return VideoInfo(
        rel_path=rel_path,
        title=title,
        resolution=resolution,
        frame_rate=frame_rate,
        bitrate_kbps=bitrate_kbps,
        video_codec=video_codec,
        audio_codec=audio_codec,
        audio_channels=audio_channels,
        audio_sample_rate=audio_sample_rate,
        duration=duration,
        file_size=file_size,
        is_passing=is_passing,
        sample_rate_passing=sample_rate_passing,
        bitrate_std=bitrate_std,
        sample_rate_std=sample_rate_std,
        full_path=full_path,
    )


def _cell_fg(info: VideoInfo, key: str) -> str | None:
    """决定一个 cell 的前景色。None 表示用系统默认色。"""
    if info.is_passing:
        return VideoGridView.FG_PASS

    failing = {'title', 'result'}
    if info.bitrate_kbps < info.bitrate_std:
        failing.add('bitrate')
    if not info.sample_rate_passing:
        failing.add('audio_sample_rate')

    return VideoGridView.FG_FAIL if key in failing else None


class VideoGridView:
    """自绘的可滚动表格，每个 cell 独立控制 fg。"""

    FG_PASS = '#228B22'
    FG_FAIL = '#DC143C'
    GRAB_WIDTH = 10

    def __init__(self, parent: tk.Widget, columns: dict[str, tuple[str, int]]):
        """
        parent: 父容器（ttk.Frame）
        columns: {'key': ('中文表头', width_in_pixels)}
        """
        self.parent = parent
        # 拷贝一份以避免拖动列宽时回写到外部 dict
        self.columns = {k: (h, w) for k, (h, w) in columns.items()}
        self._rows: list[ttk.Frame] = []
        self._resize_state: dict | None = None
        self._cell_font = tkfont.Font(font=('TkDefaultFont', 9))
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

        # 表头
        self._build_header()

        # 滚动区
        body = ttk.Frame(self.outer)
        body.pack(side='top', fill='both', expand=True)

        self.vsb = ttk.Scrollbar(body, orient='vertical')
        self.vsb.pack(side='right', fill='y')
        self.hsb = ttk.Scrollbar(body, orient='horizontal')
        self.hsb.pack(side='bottom', fill='x')

        self.canvas = tk.Canvas(body, highlightthickness=0)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.vsb.config(command=self.canvas.yview)
        self.hsb.config(command=self.canvas.xview)
        self.canvas.config(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)

        self.data_frame = ttk.Frame(self.canvas)
        self._canvas_window = self.canvas.create_window(
            (0, 0), window=self.data_frame, anchor='nw'
        )
        self.canvas.bind('<Configure>', self._on_canvas_configure)
        self.data_frame.bind('<Configure>', self._on_data_configure)
        self._bind_mousewheel()

    def _build_header(self):
        self.header_frame = ttk.Frame(self.outer)
        self.header_frame.pack(side='top', fill='x')

        # 表头 grid 里：偶数列 = 列标签，奇数列 = 列间拖拽手柄。
        # 数据行 grid 里没有手柄，所以列索引仍然是 0,1,2,...
        keys = list(self.columns.keys())
        n = len(keys)
        for i, key in enumerate(keys):
            heading, width = self.columns[key]
            col_idx = i * 2

            anchor = self._column_anchor(key)
            display_heading = self._truncate_text(heading, width)
            lbl = tk.Label(
                self.header_frame,
                text=display_heading,
                font=('TkDefaultFont', 9),
                anchor=anchor,
                padx=4, pady=2,
                relief='raised', bd=1,
            )
            lbl.grid(row=0, column=col_idx, sticky='nsew')
            self.header_frame.grid_columnconfigure(col_idx, minsize=width, weight=1)

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
        # 更新内部宽度（数据行 add_row 会从这里读）
        heading = self.columns[key][0]
        self.columns[key] = (heading, new_width)
        keys = list(self.columns.keys())
        data_idx = keys.index(key)
        header_col_idx = data_idx * 2
        self.header_frame.grid_columnconfigure(header_col_idx, minsize=new_width)
        # 数据行 cell 现在在 col data_idx*2（与表头列结构对齐）
        for row_frame in self._rows:
            row_frame.grid_columnconfigure(data_idx * 2, minsize=new_width)
        # 已存在的数据行：该列 cell 文本按新列宽重新截断
        for row_frame in self._rows:
            k, full_text, cell = row_frame._cells[data_idx]
            new_text = self._truncate_text(full_text, new_width)
            if cell.cget('text') != new_text:
                cell.config(text=new_text)

    def _end_col_resize(self):
        self._resize_state = None

    def _truncate_text(self, text: str, col_width: int) -> str:
        """把 text 截断到不超过列宽（扣去 padx + bd）。

        快路径：按最宽字符估算宽度，若确定能装下就跳过 measure（绝大多数 cell 走这里）。
        慢路径：measure 验证 + 二分查找前缀长度。
        """
        max_width = max(0, col_width - 10)
        # 9pt 下 ASCII ~7px，CJK ~14px；纯 ASCII 用更小的上界可以跳过更多 measure
        has_cjk = any(ord(c) >= 0x80 for c in text)
        per_char = 7 if not has_cjk else 14
        if len(text) * per_char <= max_width:
            return text
        if self._cell_font.measure(text) <= max_width:
            return text
        ellipsis = '...'
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._cell_font.measure(text[:mid] + ellipsis) <= max_width:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + ellipsis

    @staticmethod
    def _column_anchor(key: str) -> str:
        if key == 'title':
            return 'w'
        return 'center'

    def _on_canvas_configure(self, event):
        # 横向：让 inner frame 至少和 canvas 等宽（填满）；但当内容（列总 minsize）
        # 比 canvas 宽时，保持 natural 宽度，让横向滚动条接管
        natural = self.data_frame.winfo_reqwidth()
        self.canvas.itemconfigure(
            self._canvas_window, width=max(event.width, natural)
        )

    def _on_data_configure(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

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

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

    def _on_mousewheel_linux(self, event):
        delta = -1 if event.num == 4 else 1
        self.canvas.yview_scroll(delta, 'units')

    def clear(self):
        """清空所有数据行，滚动条复位。"""
        for child in self.data_frame.winfo_children():
            child.destroy()
        self._rows.clear()
        self.canvas.yview_moveto(0)

    def add_row(self, info: VideoInfo):
        """往表格里追加一行，cell 颜色由 _cell_fg 决定。

        列结构与表头一致：cell 在 col i*2，间隔列 col i*2+1 占 8px。
        这样表头 (label+grab) 和数据行 (cell+间隔) 的列边界完全重合。
        """
        row_frame = ttk.Frame(self.data_frame)
        row_frame.pack(fill='x')
        self._rows.append(row_frame)

        n = len(self.columns)
        # (key, full_text, label) — 列宽变化时按 full_text 重新截断
        row_cells: list[tuple[str, str, tk.Label]] = []
        for i, (key, (_heading, width)) in enumerate(self.columns.items()):
            full_text = self._format_cell(info, key)
            text = self._truncate_text(full_text, width)
            anchor = self._column_anchor(key)
            fg = _cell_fg(info, key)
            cell = tk.Label(
                row_frame,
                text=text,
                anchor=anchor,
                padx=4, pady=2,
                fg=fg if fg is not None else 'black',
            )
            cell.grid(row=0, column=i * 2, sticky='nsew')
            row_frame.grid_columnconfigure(i * 2, minsize=width, weight=1)
            row_cells.append((key, full_text, cell))
            if i < n - 1:
                # 与表头 grab 列同宽，保证列边界对齐；不放 widget 仅占空间
                # weight=0: 不参与拉伸/压缩, 始终保持 GRAB_WIDTH
                row_frame.grid_columnconfigure(i * 2 + 1, minsize=self.GRAB_WIDTH, weight=0)
        row_frame._cells = row_cells

    @staticmethod
    def _format_cell(info: VideoInfo, key: str) -> str:
        """把 VideoInfo 字段映射成 cell 显示文本。"""
        if key == 'title':
            rel = info.rel_path.rstrip('/')
            return f"{rel}/{info.title}" if rel != '.' else f"./{info.title}"
        if key == 'bitrate':
            return f"{info.bitrate_kbps:.0f}"
        if key == 'result':
            return "达标" if info.is_passing else "不达标"
        return {
            'resolution': info.resolution,
            'frame_rate': info.frame_rate,
            'video_codec': info.video_codec,
            'audio_codec': info.audio_codec,
            'audio_channels': info.audio_channels,
            'audio_sample_rate': info.audio_sample_rate,
            'duration': info.duration,
            'file_size': info.file_size,
        }[key]

    def bind_drop(self, callback):
        """把拖拽事件绑定到 outer（覆盖表头 + 画布，空表也能接住）。"""
        self.outer.drop_target_register('DND_Files')
        self.outer.dnd_bind('<<Drop>>', callback)


def scan_video_files(directory: str, recursive: bool) -> list[str]:
    """扫描目录获取视频文件列表"""
    files = []
    if recursive:
        for root, _dirs, filenames in os.walk(directory):
            for f in filenames:
                if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS:
                    files.append(os.path.join(root, f))
    else:
        try:
            for f in os.listdir(directory):
                full = os.path.join(directory, f)
                if os.path.isfile(full) and os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS:
                    files.append(full)
        except OSError:
            pass
    return files


class VideoCheckerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("视频检测 v2.1")
        self.root.geometry("1200x700")
        self.root.minsize(900, 500)

        self.video_results: list[VideoInfo] = []
        self.scanning = False
        self.result_queue: queue.Queue = queue.Queue()

        self._create_widgets()

    def _create_widgets(self):
        # === Row 1: 路径输入行 ===
        top_frame = ttk.Frame(self.root, padding=5)
        top_frame.pack(fill='x')

        ttk.Label(top_frame, text="文件路径:").pack(side='left')
        self.path_var = tk.StringVar()
        self.path_entry = ttk.Entry(top_frame, textvariable=self.path_var, width=50)
        self.path_entry.pack(side='left', padx=(5, 2), fill='x', expand=True)

        ttk.Button(top_frame, text="浏览", command=self._browse_path).pack(side='left', padx=2)

        self.recursive_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top_frame, text="子文件夹", variable=self.recursive_var
        ).pack(side='left', padx=(10, 2))

        self.scan_btn = ttk.Button(top_frame, text="开始检测", command=self._start_scan)
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

        ttk.Button(std_frame, text="清除记录", command=self._clear_records).pack(
            side='left', padx=(15, 0)
        )

        # === Main: 结果表格（自绘 VideoGridView） ===
        tree_frame = ttk.Frame(self.root)
        tree_frame.pack(fill='both', expand=True, padx=5, pady=5)

        columns = (
            'title', 'resolution', 'frame_rate', 'bitrate',
            'video_codec', 'audio_codec', 'audio_channels', 'audio_sample_rate',
            'duration', 'file_size', 'result',
        )
        headers = {
            'title': ('标题', 280),
            'resolution': ('分辨率', 90),
            'frame_rate': ('帧率', 80),
            'bitrate': ('码率(kbps)', 90),
            'video_codec': ('视频编码', 80),
            'audio_codec': ('音频编码', 80),
            'audio_channels': ('声道数', 55),
            'audio_sample_rate': ('采样率(kHz)', 90),
            'duration': ('时长', 75),
            'file_size': ('文件大小', 80),
            'result': ('结果', 70),
        }

        self.grid = VideoGridView(tree_frame, headers)
        self.grid.pack(fill='both', expand=True)

        # 全局快捷键
        self.root.bind('<Control-o>', lambda _e: self._browse_path())

        # === 状态栏 ===
        self.status_var = tk.StringVar(value="就绪")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief='sunken', anchor='w', padding=3)
        status_bar.pack(fill='x', padx=5)

        # === Bottom: 移动文件行 ===
        bottom_frame = ttk.Frame(self.root, padding=5)
        bottom_frame.pack(fill='x')

        ttk.Label(bottom_frame, text="目标路径:").pack(side='left')
        self.dest_var = tk.StringVar()
        self.dest_entry = ttk.Entry(bottom_frame, textvariable=self.dest_var, width=50)
        self.dest_entry.pack(side='left', padx=(5, 2), fill='x', expand=True)

        ttk.Button(bottom_frame, text="浏览", command=self._browse_dest).pack(side='left', padx=2)

        self.move_btn = ttk.Button(bottom_frame, text="移动达标文件", command=self._move_passing_files)
        self.move_btn.pack(side='left', padx=(10, 0))
        self.move_all_btn = ttk.Button(bottom_frame, text="移动全部文件", command=self._move_all_files)
        self.move_all_btn.pack(side='left', padx=(5, 0))

        # 启用拖拽文件到表格
        self.grid.bind_drop(self._on_file_drop)

    # ---- 事件处理 ----

    def _browse_path(self):
        folder = filedialog.askdirectory(title="选择视频文件夹")
        if folder:
            self.path_var.set(folder)
            self.dest_var.set(os.path.join(folder, "Checked"))

    def _browse_dest(self):
        folder = filedialog.askdirectory(title="选择目标文件夹")
        if folder:
            self.dest_var.set(folder)

    def _start_scan(self):
        if self.scanning:
            return

        directory = self.path_var.get().strip()
        if not directory or not os.path.isdir(directory):
            messagebox.showerror("错误", "请输入有效的文件夹路径。")
            return

        try:
            bitrate_std = float(self.bitrate_var.get().strip())
            if bitrate_std <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "码率标准必须为正数。")
            return

        try:
            sample_rate_std = float(self.sample_rate_var.get().strip())
            if sample_rate_std <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "采样率标准必须为正数。")
            return

        # 设置默认目标路径
        if not self.dest_var.get().strip():
            self.dest_var.set(os.path.join(directory, "Checked"))

        self.scanning = True
        self.scan_btn.config(state='disabled')
        self.video_results.clear()
        # 清空表格
        self.grid.clear()

        self.result_queue = queue.Queue()

        t = threading.Thread(
            target=self._scan_worker,
            args=(directory, self.recursive_var.get(), bitrate_std, sample_rate_std),
            daemon=True,
        )
        t.start()
        self._check_queue()

    def _scan_worker(self, directory: str, recursive: bool, bitrate_std: float, sample_rate_std: float):
        """扫描工作线程"""
        self.result_queue.put(('status', '正在扫描文件列表...'))
        files = scan_video_files(directory, recursive)
        total = len(files)

        if total == 0:
            self.result_queue.put(('status', '未找到视频文件'))
            self.result_queue.put(('done', None))
            return

        for i, fp in enumerate(files, 1):
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
                    self.status_var.set(payload)
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
        self.grid.add_row(info)

    def _scan_complete(self):
        """扫描完成处理"""
        self.scanning = False
        self.scan_btn.config(state='normal')

        total = len(self.video_results)
        passed = sum(1 for v in self.video_results if v.is_passing)
        failed = total - passed
        self.status_var.set(f"检测完成：共 {total} 个视频文件，{passed} 个达标，{failed} 个不达标")

    def _clear_records(self):
        """清空检测记录与目标路径（不影响阈值输入）。"""
        if self.scanning:
            messagebox.showinfo("提示", "正在检测中，请等待完成后再清除。")
            return
        self.video_results.clear()
        self.grid.clear()
        self.dest_var.set('')
        self.status_var.set("已清除记录")

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
        try:
            bitrate_std = float(self.bitrate_var.get().strip())
            if bitrate_std <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "码率标准必须为正数。")
            return

        try:
            sample_rate_std = float(self.sample_rate_var.get().strip())
            if sample_rate_std <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "采样率标准必须为正数。")
            return

        # 解析拖拽的文件列表（Windows 格式）
        files = event.data.strip()
        if not files:
            return

        # 处理 {file1} {file2} ... 格式
        import re
        file_list = re.findall(r'\{([^}]+)\}', files)
        if not file_list:
            # 没有大括号，直接作为文件路径
            file_list = [files]

        video_files = [f for f in file_list if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS]
        if not video_files:
            messagebox.showinfo("提示", "没有找到视频文件。")
            return

        # 目标路径默认在第一个文件所在目录下追加 Checked 子目录
        self.dest_var.set(os.path.join(os.path.dirname(video_files[0]), "Checked"))

        self.scanning = True
        self.scan_btn.config(state='disabled')
        self.video_results.clear()
        self.grid.clear()

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
        self.result_queue.put(('status', f'正在检测 {len(files)} 个文件...'))
        base_path = os.path.dirname(files[0]) if files else '.'

        for i, fp in enumerate(files, 1):
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
