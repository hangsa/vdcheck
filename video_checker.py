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


@lru_cache(maxsize=1)
def get_ffmpeg_path() -> str:
    """获取 ffmpeg 路径 (优先使用打包的, fallback 到 PATH).

    与 get_ffprobe_path 完全对称: build.bat 下载 FFmpeg-master-latest 时
    整个 bin/ 目录被拷到 ffprobe/, ffmpeg.exe 已经在那里。
    """
    if getattr(sys, 'frozen', False):
        base_dir = sys._MEIPASS
        local_ffmpeg = os.path.join(base_dir, 'ffprobe', 'ffmpeg.exe')
        if os.path.exists(local_ffmpeg):
            return local_ffmpeg
    return shutil.which('ffmpeg') or 'ffmpeg'


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
    container_format: str = 'N/A'  # NEW: 容器格式（如 'mp4'/'matroska'），ffprobe format_name 优先，缺则回退到扩展名
    # 排序用原始数值 (parse_video_info 写入; 默认值仅供测试夹具)
    frame_rate_fps: float = 0.0
    sample_rate_khz: float = 0.0


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


def parse_time_token(token: str, fps: float) -> float:
    """把 'HH:MM:SS:frame' 解析为秒 (float).

    帧号除以 fps 得到该帧的秒偏移。非法格式 / 越界 / fps<=0 抛 ValueError。
    """
    parts = token.strip().split(':')
    if len(parts) != 4:
        raise ValueError(f"时间点格式应为 HH:MM:SS:frame, 得到: {token!r}")
    try:
        h, m, s, f = (int(p) for p in parts)
    except ValueError:
        raise ValueError(f"时间点含非数字段: {token!r}")
    if h < 0 or m < 0 or s < 0 or f < 0 or m > 59 or s > 59:
        raise ValueError(f"时间点数值越界: {token!r}")
    if fps <= 0:
        raise ValueError(f"视频帧率无效 ({fps}), 无法解析 frame 字段")
    return h * 3600 + m * 60 + s + f / fps


def time_token_to_string(token: str) -> str:
    """把 'HH:MM:SS:frame' 中的 ':' 替换为 '-'.

    用于文件名 (Windows 保留 ':') 和进度弹窗列头显示。
    """
    return token.replace(':', '-')


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


def extract_frame(input_path: str, seconds: float, output_path: str) -> bool:
    """用 ffmpeg 在 seconds 处抽 1 帧, 缩放到 160 px 宽, JPEG q=5 写入 output_path.

    磁盘上保留的就是 160 px 缩略图, 进度弹窗直接 PhotoImage(file=...) 加载。
    ffmpeg 不可用 / 文件损坏 / 超时 / 跨平台 AttributeError 均返回 False。
    """
    try:
        ffmpeg = get_ffmpeg_path()
        kwargs = {
            'stdin': subprocess.DEVNULL,
            'stdout': subprocess.DEVNULL,
            'stderr': subprocess.PIPE,
            'timeout': 30,
        }
        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            kwargs['startupinfo'] = startupinfo
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            [
                ffmpeg, '-y',
                '-ss', f'{seconds:.6f}',
                '-i', input_path,
                '-frames:v', '1',
                '-vf', 'scale=160:-1',
                '-q:v', '5',
                output_path,
            ],
            **kwargs,
        )
        return result.returncode == 0 and os.path.exists(output_path)
    except (subprocess.TimeoutExpired, OSError, AttributeError):
        return False


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
    fps = 0.0
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
    sample_rate_hz = 0.0
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

    # 容器格式: 优先在 format_name 列表里匹配文件扩展名, 否则取第一项;
    # format_name 缺失/为空时回退到文件扩展名; 仍无则 'N/A'
    raw_format = fmt.get('format_name', '')
    ext = os.path.splitext(full_path)[1].lstrip('.').lower()
    container_format = ''
    if raw_format:
        tokens = raw_format.split(',')
        # 优先选与扩展名匹配的 token (例如 mp4 文件的 format_name='mov,mp4,...' → 'mp4')
        if ext and ext in tokens:
            container_format = ext
        else:
            container_format = tokens[0]
    if not container_format:
        container_format = ext if ext else 'N/A'

    is_passing = (bitrate_kbps >= bitrate_std) and sample_rate_passing

    return VideoInfo(
        rel_path=rel_path,
        title=title,
        resolution=resolution,
        frame_rate=frame_rate,
        frame_rate_fps=fps,
        bitrate_kbps=bitrate_kbps,
        video_codec=video_codec,
        audio_codec=audio_codec,
        audio_channels=audio_channels,
        audio_sample_rate=audio_sample_rate,
        sample_rate_khz=sample_rate_hz / 1000.0,
        duration=duration,
        file_size=file_size,
        is_passing=is_passing,
        sample_rate_passing=sample_rate_passing,
        bitrate_std=bitrate_std,
        sample_rate_std=sample_rate_std,
        full_path=full_path,
        container_format=container_format,  # NEW
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


# --- 列表头点击排序辅助函数 ---

# 4 个可点击排序的列表头 (其他列表头点击无反应)。
_SORTABLE_COLUMNS = frozenset({
    'result', 'bitrate', 'frame_rate', 'audio_sample_rate',
})


def _extract_sort_key(info: VideoInfo, key: str):
    """从 VideoInfo 取出用于排序的原始数值 / 布尔值。未知列抛 KeyError。"""
    if key == 'result':
        return info.is_passing
    if key == 'bitrate':
        return info.bitrate_kbps
    if key == 'frame_rate':
        return info.frame_rate_fps
    if key == 'audio_sample_rate':
        return info.sample_rate_khz
    raise KeyError(key)


def _next_sort_state(
    current_key: str | None, current_desc: bool, clicked_key: str,
) -> tuple[str | None, bool]:
    """3 态循环 + 跨列切换重置到升序。返回 (new_key, new_desc)。"""
    if clicked_key not in _SORTABLE_COLUMNS:
        return current_key, current_desc
    if current_key != clicked_key:
        return clicked_key, False   # 跨列 → 升序开始
    if not current_desc:
        return clicked_key, True    # 升序 → 降序
    return None, False             # 降序 → 取消


def _header_text_with_arrow(base_text: str, is_active: bool, desc: bool) -> str:
    """表头显示文本: 活动列附 ▲/▼, 否则原样。"""
    if not is_active:
        return base_text
    return f"{base_text} ▼" if desc else f"{base_text} ▲"


class VideoGridView:
    """自绘的可滚动表格，每个 cell 独立控制 fg。虚拟滚动版。"""

    FG_PASS = '#228B22'
    FG_FAIL = '#DC143C'
    GRAB_WIDTH = 10
    # 池初始大小: 视区一行约占 24-26 px, 默认窗口 700 - 表头 ≈ 660, 默认可视
    # 行约 25 行; +2×BUFFER 上下预留 = 池 37. 取整 40, 留 ~30% 余量给超长文件名
    # 带来的行高膨胀、阈值放大场景。
    POOL_SIZE = 40
    VISIBLE_BUFFER = 6
    RESIZE_THROTTLE_MS = 33

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
        # 备份原生 set, 在 command lambda 里同时更新 scrollbar 与排程 render。
        self._vsb_set = self.vsb.set
        self.vsb.config(command=lambda *a: (
            self.canvas.yview(*a), self._schedule_render()))
        self.hsb.config(command=lambda *a: (
            self.canvas.xview(*a), self._schedule_render()))
        self.canvas.config(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)

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
            self._create_pool_widget()
        self._bottom_spacer = tk.Frame(self.data_frame, height=0)
        self._bottom_spacer.pack(fill='x')
        # 触发一次 idle 等 widget mapped 后测量实际行高
        self.canvas.update_idletasks()
        self._measure_row_height()

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
            cell.grid(row=0, column=i * 2, sticky='nsew')
            # 用 _col_widths (而非 self.columns) 同步列宽真理来源
            row_frame.grid_columnconfigure(i * 2, minsize=self._col_widths[key], weight=1)
            row_cells.append((key, cell))
            if i < n - 1:
                # 与表头 grab 列同宽, 保证列边界对齐; 不放 widget 仅占空间
                # weight=0: 不参与拉伸/压缩, 始终保持 GRAB_WIDTH
                row_frame.grid_columnconfigure(i * 2 + 1, minsize=self.GRAB_WIDTH, weight=0)
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
        """列总宽 + spacer 总宽."""
        if not self._col_widths:
            return 0
        cols = sum(self._col_widths.values())
        spaces = max(0, len(self._col_widths) - 1) * self.GRAB_WIDTH
        return cols + spaces

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
        header_col_idx = data_idx * 2
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
            widget.grid_columnconfigure(data_idx * 2, minsize=new_width)
        # scrollregion 横向范围也随之变化
        self.canvas.configure(scrollregion=(
            0, 0, max(self.canvas.winfo_width(), self._compute_total_width()),
            max(0, len(self._data) * self._row_height),
        ))

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
        # 横向: 让 inner frame 至少和 canvas 等宽 (填满); 但当内容 (列总 minsize)
        # 比 canvas 宽时, 保持 natural 宽度, 让横向滚动条接管。
        # 虚拟滚动下 data_frame 实际宽 = 池 widget 高度累计, 故用 _compute_total_width
        # 与 canvas 等宽取大。
        natural = self._compute_total_width()
        self.canvas.itemconfigure(
            self._canvas_window, width=max(event.width, natural)
        )
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

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')
        # yview 改变后哪些 data 在可见区需要重算 → 排程 render
        self._schedule_render()

    def _on_mousewheel_linux(self, event):
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
            self._first_idx, self._last_idx = 0, 0
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
        first_frac, _ = self.canvas.yview()
        # 防止 n < visible 时 first_frac * (n-visible) < 0
        first_frac = max(0.0, min(1.0, first_frac))
        raw_first = int(first_frac * max(0, n - visible))
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

        # 高度 spacer: 模拟被滚走的行与未滚到的行
        self._top_spacer.configure(height=first * self._row_height)
        self._bottom_spacer.configure(
            height=max(0, (n - last) * self._row_height)
        )

        # 池 widget slot → data 绑定 (倒序填充, 视觉顺序不变)
        for slot, data_idx in enumerate(range(first, last)):
            widget = self._widget_pool[slot]
            info = self._data[data_idx]
            # 未变化就跳过 cell 重写; 这是热路径, 大量命中此分支。
            if (widget._bound_data_idx == data_idx
                    and widget._info is info):
                continue
            widget._bound_data_idx = data_idx
            widget._info = info
            for key, cell in widget._cells:
                full_text = self._format_cell(info, key)
                width = self._col_widths[key]
                new_text = self._truncate_text(full_text, width)
                if cell.cget('text') != new_text:
                    cell.config(text=new_text)
                fg = _cell_fg(info, key)
                new_fg = fg if fg is not None else 'black'
                if cell.cget('fg') != new_fg:
                    cell.config(fg=new_fg)

        # scrollregion: 显式计算, 不用 bbox('all'); 虚拟滚动中 bbox 只含
        # mapped widget, 但用户感知到的总高是 N × ROW_HEIGHT。
        total_h = max(0, n * self._row_height)
        total_w = max(self.canvas.winfo_width(), self._compute_total_width())
        self.canvas.configure(scrollregion=(0, 0, total_w, total_h))

    def clear(self):
        """清空所有数据行，滚动条复位。"""
        self._data.clear()
        for widget in self._widget_pool:
            widget._info = None
            widget._bound_data_idx = None
            for _key, cell in widget._cells:
                cell.config(text='', fg='black')
        self.canvas.yview_moveto(0)
        # 同步 spacing / scrollregion
        self._top_spacer.configure(height=0)
        self._bottom_spacer.configure(height=0)
        self.canvas.configure(scrollregion=(
            0, 0, self.canvas.winfo_width(), 0,
        ))
        self._first_idx = self._last_idx = 0
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
            'container_format': info.container_format,
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
        self.root.title("视频检测 v2.2")
        self.root.geometry("1280x700")
        self.root.minsize(1000, 500)

        self.video_results: list[VideoInfo] = []
        self.scanning = False
        self.cancel_event = threading.Event()
        self.total_files: int = 0
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

        columns = (
            'title', 'resolution', 'frame_rate', 'bitrate',
            'video_codec', 'container_format', 'audio_codec', 'audio_channels',
            'audio_sample_rate', 'duration', 'file_size', 'result',
        )
        headers = {
            'title': ('标题', 250),
            'resolution': ('分辨率', 90),
            'frame_rate': ('帧率', 75),
            'bitrate': ('码率(kbps)', 85),
            'video_codec': ('视频编码', 80),
            'container_format': ('封装', 75),
            'audio_codec': ('音频编码', 80),
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
            self.dest_var.set(os.path.join(folder, "Checked").replace('/', '\\'))

    def _browse_dest(self):
        folder = filedialog.askdirectory(title="选择目标文件夹")
        if folder:
            self.dest_var.set(folder.replace('/', '\\'))

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
            self.dest_var.set(os.path.join(directory, "Checked").replace('/', '\\'))

        self.cancel_event.clear()
        self.scanning = True
        self.total_files = 0
        self.scan_btn.config(text='检测中')
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
        cancelled = self.cancel_event.is_set()
        self.scanning = False
        self.scan_btn.config(state='normal', text='开始检测')

        # 扫描中途按插入序追加结果; 若已激活排序, 扫描结束统一 reorder 一次
        if self.grid.is_sorted():
            self.grid.reorder()

        completed = len(self.video_results)
        passed = sum(1 for v in self.video_results if v.is_passing)
        failed = completed - passed
        if cancelled:
            queued = self.total_files
            pending = max(queued - completed, 0)
            self.status_var.set(
                f"已停止检测：共 {queued} 个视频文件，"
                f"已完成 {completed} 个文件，"
                f"{passed} 个达标，{failed} 个不达标，"
                f"{pending} 个未检测"
            )
        else:
            self.status_var.set(f"检测完成：共 {completed} 个视频文件，{passed} 个达标，{failed} 个不达标")

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
        if self.scanning:
            messagebox.showinfo("提示", "正在检测中，请等待完成后再添加文件。")
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

        # 解析拖拽的文件列表: 支持 Windows ``{file1} {file2}`` 与 macOS / Linux 空格分隔
        file_list = _parse_drop_data(event.data)
        if not file_list:
            return

        video_files = [f for f in file_list if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS]
        if not video_files:
            messagebox.showinfo("提示", "没有找到视频文件。")
            return

        # 在文件路径输入框显示第一个被拖入的文件所在目录（不含文件名）,
        # 目标路径默认在该目录下追加 Checked 子目录。Windows 系统下统一
        # 使用反斜杠, 避免路径中斜杠与反斜杠交替出现。
        self.path_var.set(os.path.dirname(video_files[0]).replace('/', '\\'))
        self.dest_var.set(
            os.path.join(os.path.dirname(video_files[0]), "Checked").replace('/', '\\')
        )

        self.cancel_event.clear()
        self.scanning = True
        self.scan_btn.config(text='检测中')
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
