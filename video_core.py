"""视频码率检查器的纯逻辑层 —— 与界面无关的计算都在这儿。

Tkinter 版和 PySide6/QTableView 版共用同一份: 凡是不看界面的计算都放这儿,
纯函数不用建 GUI 就能测 (断言脚本直接 import 本模块)。
"""

import json
import math
import ntpath
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

# 视频扩展名集合
VIDEO_EXTENSIONS = {
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.ts', '.m2ts',
    '.mpg', '.mpeg', '.webm', '.3gp', '.m4v', '.vob', '.ogv',
    '.rm', '.rmvb', '.asf', '.divx', '.f4v', '.mxf', '.mts',
    '.m1v', '.m2v', '.mpv', '.qt', '.y4m', '.nut', '.dv',
    '.tp', '.trp', '.hevc', '.h264', '.h265', '.264', '.265',
}

# 表格着色用的两个颜色。原先挂在 VideoGridView 上, 抽到模块级后
# _cell_fg 就能在 core 里用了 (它只依赖这两个常量)。
FG_PASS = '#228B22'   # 绿字 (达标)
FG_FAIL = '#DC143C'   # 红字 (不达标)

# 阈值默认值。原先 video_qt.py 与 video_checker.py (Tk 版) **各有一份
# 字面量** —— 改一处忘一处就会静默不一致。Tk 版删掉后, core 是唯一定义处,
# 界面层一律从这里 import。
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


def format_title(info: VideoInfo) -> str:
    """标题列文本: ./A/B/name.mp4 (相对路径 + 文件名)。

    rel_path 在跨盘符时会被 parse_video_info 设成绝对全路径
    (os.path.relpath 抛 ValueError 的分支, 见本文件 parse_video_info),
    那种情况下 rel_path 本身已含文件名, 直接返回, 不再拼 title ——
    否则会输出 "D:\\videos\\a.mp4/a.mp4" 这种路径重复。

    用 ntpath 而非 os.path: 绝对路径形态只在 Windows 产生, 但测试跑在
    macOS 上, os.path.isabs('D:\\...') 在 posix 下返回 False 会假绿。
    ntpath.isabs 对盘符路径与 UNC 路径两平台行为一致。
    """
    rel = info.rel_path.rstrip('/\\')
    if ntpath.isabs(rel):
        return rel
    if rel in ('', '.'):
        return f"./{info.title}"
    return f"{rel}/{info.title}"


def truncate_text(text: str, col_width: int, measure: Callable[[str], int]) -> str:
    """按像素宽截断文本并加省略号。max_width = col_width - 10 (扣 padx + bd)。

    measure: 文本像素宽度的测量函数。Tk 侧传 tkfont.Font.measure,
    Qt 侧传 QFontMetrics.horizontalAdvance —— 截断口径因此完全一致,
    且这个函数不需要建 GUI 就能测。

    提到模块级是因为主网格和定点截图弹窗都要用同一套截断规则。

    快路径：按最宽字符估算宽度，若确定能装下就跳过 measure（绝大多数 cell 走这里）。
    慢路径：measure 验证 + 二分查找前缀长度。
    """
    max_width = max(0, col_width - 10)
    # 9pt 下 ASCII ~7px，CJK ~14px；纯 ASCII 用更小的上界可以跳过更多 measure
    has_cjk = any(ord(c) >= 0x80 for c in text)
    per_char = 7 if not has_cjk else 14
    if len(text) * per_char <= max_width:
        return text
    if measure(text) <= max_width:
        return text
    ellipsis = '...'
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if measure(text[:mid] + ellipsis) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ellipsis


def parse_threshold(text: str) -> float | None:
    """解析单个阈值。非法返回 None。

    原来是内联在 _start_scan 与 _on_file_drop 里各写一遍, 且只判 `> 0`
    —— float() 接受 inf / nan, 而 inf > 0 成立 → 所有行永远不达标。
    """
    try:
        value = float(text.strip())
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def run_ffprobe(file_path: str) -> dict | None:
    """调用 ffprobe 获取视频文件信息"""
    try:
        ffprobe_path = get_ffprobe_path()
        kwargs = {
            'stdin': subprocess.DEVNULL,
            'stdout': subprocess.PIPE,
            'stderr': subprocess.PIPE,
            'timeout': 30,
        }
        # 隐藏控制台只在 Windows 上有意义; 原来无条件构造 STARTUPINFO,
        # macOS/Linux 上 AttributeError 逃出下面的 except 列表 → worker
        # 线程静默死亡 → UI 永久卡在「检测中」。与 extract_frame 保持一致。
        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            kwargs['startupinfo'] = startupinfo
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

        result = subprocess.run(
            [
                ffprobe_path, '-v', 'quiet',
                '-print_format', 'json',
                '-show_format', '-show_streams',
                file_path,
            ],
            **kwargs,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout.decode('utf-8', errors='replace'))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, AttributeError):
        return None


# 抽帧输出宽度 (px), 进度弹窗 cell 显示用 subsample(2) 缩到一半 (EXTRACT_THUMB_W/2)
# 单帧 PNG 100-300 KB, 500 帧 50-150 MB; 弹窗可点 cell 看原图
EXTRACT_THUMB_W = 640


def extract_frame(input_path: str, seconds: float, output_path: str) -> bool:
    """用 ffmpeg 在 seconds 处抽 1 帧, 缩放到 EXTRACT_THUMB_W px 宽, PNG 写入 output_path.

    磁盘上保留的就是大图, 进度弹窗 cell 显示用 PhotoImage.subsample(2) 缩到一半;
    点 cell 弹 EnlargeDialog 看原图。
    输出格式选 PNG 而非 JPEG: Tk 的 PhotoImage 原生只支持 GIF/PGM/PPM/PNG,
    不能读 JPEG, 用 PNG 才能让弹窗直接显示。
    640x360 PNG 单帧 100-300 KB, 500 帧约 50-150 MB, 可接受。
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
                '-vf', f'scale={EXTRACT_THUMB_W}:-1',
                '-c:v', 'png',
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
    # 注意: 不能用 .get(k, default) —— 键存在但值为 null 时返回 None,
    # 'None'.split('/') 抛 AttributeError, 而下面的 except 只接 ValueError/
    # ZeroDivisionError, 会逃出本函数打死扫描线程 (UI 卡在「检测中」)。
    # `or` 同时把 None 和空串归一到 '0/1' → fps=0.0 → 显示 N/A。
    r_frame_rate = video_stream.get('r_frame_rate') or '0/1'
    fps = 0.0
    try:
        num, den = r_frame_rate.split('/')
        # 分母为 0 ('0/0') 不会抛异常, 只是算不出帧率
        fps = float(num) / float(den) if float(den) != 0 else 0.0
        # 帧率 <= 0 (含 '0/0' / '0/1' / 负数) 一律显示 N/A: 显示 '0.00'
        # 会让用户以为视频真的 0 帧, 而实测这类视频在定点截图弹窗里每个
        # 时间点都抽帧失败 (parse_time_token 对 fps<=0 抛 ValueError)。
        # frame_rate_fps 仍是 0.0, 排序和抽帧的拒绝行为都不变。
        frame_rate = f"{fps:.2f}" if fps > 0 else "N/A"
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
            audio_sample_rate = f"{sample_rate_hz / 1000:.1f}"
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
        return FG_PASS

    failing = {'title', 'result'}
    if info.bitrate_kbps < info.bitrate_std:
        failing.add('bitrate')
    if not info.sample_rate_passing:
        failing.add('audio_sample_rate')

    return FG_FAIL if key in failing else None


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
