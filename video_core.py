"""视频码率检查器的纯逻辑层 —— 与界面无关的计算都在这儿。

Tkinter 版和 PySide6/QTableView 版共用同一份: 凡是不看界面的计算都放这儿,
纯函数不用建 GUI 就能测 (断言脚本直接 import 本模块)。
"""

import csv
import io
import json
import math
import ntpath
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
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
    # NEW: 色彩信息 —— ffprobe 视频流的 color_primaries (颜色基准) 与
    # color_transfer (颜色映射/传递函数)。**原样保留 ffprobe 的值**, 不加工
    # (用户 2026-10-29 明确选的: 「不加工, ffprobe 原样」) —— 所以
    # ffprobe 报 'unknown' 就显示 'unknown', 只有**键不存在**才是 'N/A'。
    color_primaries: str = 'N/A'
    color_transfer: str = 'N/A'
    # 排序用原始数值 (parse_video_info 写入; 默认值仅供测试夹具)
    frame_rate_fps: float = 0.0
    sample_rate_khz: float = 0.0
    # 视频时长 (秒, 原始值) —— **逆时间点**要用它算「片尾往前推」的实际
    # 截图时刻。duration 是给界面看的格式化字符串 ('HH:MM:SS'), 反解会
    # 丢掉小数, 所以这里另存一份精确的 (2026-11-04)。
    duration_seconds: float = 0.0


def format_frame_rate(fps: float) -> str:
    """帧率的显示格式: **3 位小数, 再把末尾的零去掉 (至少留 2 位)**。

    2 位小数会把 NTSC 的 24000/1001 (23.976…) 显示成 **23.98** ——
    而 23.98 不是一个真实存在的帧率, 看着像另一个速率 (用户 2026-11-04
    报的)。29.97 / 59.94 用 2 位刚好精确, **只有 23.976 会丢信息**。

    ⇒ 23.976 / 29.97 / 59.94 / 25.00 / 30.00
    """
    text = f'{fps:.3f}'.rstrip('0')      # '23.976' / '29.97' / '25.'
    if text.endswith('.'):
        return text + '00'                # '25.' → '25.00'
    frac = text.split('.', 1)[1]
    if len(frac) < 2:
        text += '0'                       # '29.5' → '29.50'
    return text


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


# 逆时间点的标记 (2026-11-04): 时间点字符串前面带个 'R' = 从片尾往片头数。
# 文件名里也是这个标记 ('名字_t1_R00-01-00-00.png'), 解析时原样带回。
REVERSE_MARKER = 'R'


def is_reverse_token(token: str) -> bool:
    """这个时间点是**逆时间点**吗 (从片尾往片头数)。"""
    return str(token).strip().upper().startswith(REVERSE_MARKER)


def strip_reverse_marker(token: str) -> str:
    """去掉逆时间点的 'R' 标记, 剩下 'HH:MM:SS:frame'。"""
    text = str(token).strip()
    if text[:1].upper() == REVERSE_MARKER:
        return text[1:]
    return text


def parse_time_token(token: str, fps: float) -> float:
    """把 'HH:MM:SS:frame' 解析为秒 (float).

    帧号除以 fps 得到该帧的秒偏移。非法格式 / 越界 / fps<=0 抛 ValueError。
    前面的 'R' 标记 (逆时间点) 会被自动剥掉。
    """
    parts = strip_reverse_marker(token).split(':')
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
    逆时间点的 'R' 标记**保留** ('R00:01:00:00' → 'R00-01-00-00')。
    """
    return token.replace(':', '-')


def actual_seconds_for_token(token: str, fps: float,
                             duration_seconds: float) -> float:
    """这个时间点**实际**在第几秒截图。

    正时间点 = 输入值;
    逆时间点 = 视频时长 - 输入值 (从片尾往片头数)。

    逆时间点算出来是负数 (输入值超过片长) → 抛 ValueError,
    调用方把**那一格**标成失败 (别的行不受影响)。
    """
    entered = parse_time_token(token, fps)
    if not is_reverse_token(token):
        return entered
    actual = duration_seconds - entered
    if actual < 0:
        raise ValueError(
            f'逆时间点 {strip_reverse_marker(token)} 超过片长 '
            f'({duration_seconds:.1f} 秒)')
    return actual


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
    # 快路径的 per_char 是**上界估计** —— 只有当**每个字符的真实 advance
    # <= per_char** 时, 「len(text) * per_char <= max_width → 肯定装得下」
    # 才成立。一旦它小于真实字符宽, 快路径就会放过超宽文本 —— 而 Qt 的
    # elide 是关的 (TruncatingDelegate 设的是 Qt.ElideNone), 放过去就
    # 直接溢出列宽。
    #
    # ⚠️ **换字号 / 换字体必须重新实测这两个常量。** 下面是 10pt 的实测值
    # (离屏 Sans Serif, QFontMetrics.horizontalAdvance):
    #   ASCII: 可打印字符里**最宽的是 'W' = 13px** (a=7, w=10, M=11)
    #   CJK:   '中' = 13px → 取 16 留一点余量
    # 之前这里是 8/16, 注释写着「8/16 在 10pt 下是真上界」—— **那是错的**:
    # 8 只是**平均**宽 ('a' 才 7px), 而 'W' 有 13px → 一串 'W' (例如
    # 大写拉丁文文件名) 会被快路径当成「装得下」放行, 而它真实宽 13px/字
    # → 文字溢出列宽。现在 ASCII 取 13 = 真实最大 advance → 快路径是真上界。
    has_cjk = any(ord(c) >= 0x80 for c in text)
    per_char = 13 if not has_cjk else 16
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
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        # **不要**在这里加 AttributeError: 这一支当初只可能来自未加平台
        # 守卫的 subprocess.STARTUPINFO, 守卫(见上)已经把它堵上了 →
        # 现在它只能吞掉 try 块**内部**抛的 AttributeError (比如 kwargs
        # 打错), 把真 bug 静默变成「解析失败」→ 文件被静默跳过, 没有任何
        # 诊断信息。宁可让它响着崩。
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
    ffmpeg 不可用 / 文件损坏 / 超时均返回 False。
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
    except (subprocess.TimeoutExpired, OSError):
        # **不要**在这里加 AttributeError: 这一支当初只可能来自未加平台
        # 守卫的 subprocess.STARTUPINFO, 守卫(见上)已经把它堵上了 →
        # 现在它只能吞掉 try 块**内部**抛的 AttributeError (比如 kwargs
        # 打错), 把真 bug 静默变成「抽帧失败」→ 进度弹窗那一格空着, 用户
        # 只看到「没抽到帧」, 没有任何诊断信息。宁可让它响着崩。
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
        frame_rate = format_frame_rate(fps) if fps > 0 else "N/A"
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

    # 色彩信息: 颜色基准 (color_primaries) + 颜色映射函数 (color_transfer)。
    # **原样取 ffprobe 的值** —— ffprobe 报 'unknown' 就存 'unknown';
    # 只有键不存在 (老版本 ffmpeg / 某些流) 才兜底成 'N/A'。
    # 空字符串也当'没有' (`.get(k, 'N/A') or 'N/A'`)。
    color_primaries = video_stream.get('color_primaries') or 'N/A'
    color_transfer = video_stream.get('color_transfer') or 'N/A'

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
        duration_seconds = float(duration_sec)
        duration = format_duration(duration_seconds)
    except (TypeError, ValueError):
        duration = "N/A"
        duration_seconds = 0.0

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
        duration_seconds=duration_seconds,
        file_size=file_size,
        is_passing=is_passing,
        sample_rate_passing=sample_rate_passing,
        bitrate_std=bitrate_std,
        sample_rate_std=sample_rate_std,
        full_path=full_path,
        container_format=container_format,  # NEW
        color_primaries=color_primaries,  # NEW
        color_transfer=color_transfer,  # NEW
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


# --- 已有截图的识别与匹配 (弹窗的「查看已有截图」用) ---
#
# 截图文件名规则 (生成端在 video_qt._snapshot_worker):
#     {视频标题去扩展名}_t{时间点序号}_{HH-MM-SS-FF}.png
# 例: 视频标题 "a" → "a_t1_00-00-07-24.png"
#
# 时间点里的 ':' 换成 '-' 是因为 Windows 不许文件名里带 ':'。

# base 贪婪匹配 + 回溯 → 取**最右**一次 "_t<数字>_", 与生成端严格对称
# (标题本身带 "_t1_" 也不会拆错: "a_t1" + "_t2_" + token → base="a_t1")。
# 时间点部分允许一个可选的 'R' 前缀 (逆时间点, 2026-11-04):
#   名字_t1_00-00-07-24.png   正时间点
#   名字_t1_R00-01-00-00.png  逆时间点
_SNAPSHOT_NAME_RE = re.compile(
    r'^(?P<base>.+)_t(?P<index>\d+)_(?P<token>[R]?\d+-\d+-\d+-\d+)\.png$',
    re.IGNORECASE)


def parse_snapshot_filename(name: str) -> tuple[str, str] | None:
    """把截图文件名拆成 (base, 时间点)。不符合规则 → None。

    base    = 视频标题去掉扩展名 (与生成端是**同一个字符串**)
    时间点  = ':' 形式 ('00:00:07:24') —— 与时间点输入框同格式;
              逆时间点带 'R' 前缀 ('R00:01:00:00')
    """
    m = _SNAPSHOT_NAME_RE.match(name)
    if not m:
        return None
    return m.group('base'), m.group('token').replace('-', ':')


@dataclass
class SnapshotFile:
    """截图目录里的一张已有截图。"""
    base: str       # 视频标题去扩展名
    token: str      # 时间点 (':' 形式)
    path: str       # 磁盘路径
    mtime: float    # 修改时间 (同一 base+token 有多张时取最新的那张)


def scan_existing_snapshots(output_dir: str, progress=None) -> list[SnapshotFile]:
    """扫截图目录里**符合命名规则**的 PNG。

    只看第一层 (不递归) —— 抽帧也是平铺写进 output_dir 的。
    目录不存在 → 空列表 (**不建目录**: 建目录只有 ensure_snapshot_dir
    一个口子, 这里只读)。

    progress: 可选的回调 progress(done, total) —— 处理完一个文件调一次。
    「查看已有截图」的后台线程用它报进度: 本地盘上这一步是毫秒级的,
    但**网络盘**上 getmtime 逐个往返能慢到几十秒, 主线程得知道进度。
    """
    if not output_dir or not os.path.isdir(output_dir):
        return []
    out: list[SnapshotFile] = []
    try:
        names = os.listdir(output_dir)
    except OSError:
        return []
    total = len(names)
    if progress is not None:
        progress(0, total)
    for i, name in enumerate(names, 1):
        parsed = parse_snapshot_filename(name)
        if parsed is not None:
            base, token = parsed
            path = os.path.join(output_dir, name)
            if os.path.isfile(path):
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    mtime = None
                if mtime is not None:
                    out.append(SnapshotFile(base=base, token=token,
                                            path=path, mtime=mtime))
        if progress is not None:
            progress(i, total)
    return out


def match_snapshots(files: list[SnapshotFile], base: str) -> dict[str, SnapshotFile]:
    """挑出属于某个视频 (base) 的截图: {时间点: 文件}。

    同一个时间点有多张 (改了时间点顺序重截过, 旧文件名还在) →
    取**修改时间最新**的那张。
    """
    out: dict[str, SnapshotFile] = {}
    for sf in files:
        if sf.base != base:
            continue
        old = out.get(sf.token)
        if old is None or sf.mtime > old.mtime:
            out[sf.token] = sf
    return out


def time_token_sort_key(token: str) -> tuple[int, int, int, int]:
    """时间点的排序键 (时, 分, 秒, 帧) —— **按数值比**, 不按字符串。

    字符串排序会把 '00:10:00:00' 排在 '00:02:00:00' 前面 ('1' < '2')。
    """
    nums: list[int] = []
    for part in strip_reverse_marker(token).split(':')[:4]:
        try:
            nums.append(int(part))
        except ValueError:
            nums.append(0)
    while len(nums) < 4:
        nums.append(0)
    return (nums[0], nums[1], nums[2], nums[3])


# ===================================================================
# 海报下载 (poster) —— 读表格文档 + 下图
# ===================================================================
#
# 需求 (2026-11-04): 用户给一份 csv/xls/xlsx 的清单, 里面有海报链接;
# 程序按「命名来源字段」+「海报链接字段名」命名, 把海报下载到指定目录。
#
# **零第三方依赖**: csv 用标准库, xlsx 用 zipfile + XML 自己解析。
# 旧版二进制 .xls 读不了 —— 明确报错让用户另存为 .xlsx/.csv
# (引第三方库会动 build.bat 的 pip install 与 PyInstaller 打包,
#  而打包产物只在 Windows 上验得了, 本机验不了 → 宁可少支持一种格式)。


class DocumentFormatError(Exception):
    """文档读不了 (格式不支持 / 不是表格 / 文件不在)。"""


@dataclass
class TabularDocument:
    """一张表: 字段名 + 数据行 (单元格一律是字符串)。"""
    headers: list[str]
    rows: list[list[str]]


# 海报链接字段的列名线索 (小写比对)。第一个命中的列就是默认项。
POSTER_URL_HINTS = ('url', 'link', '海报', '图片', '封面', 'poster', 'image',
                    '缩略图', '图集')
# 「命名来源」字段 (clipname) 的列名线索 —— 决定**海报地址**(文件夹名),
# 那一列的值通常是整个片子都一样。
CLIPNAME_HINTS = ('clipname', 'clip_name', '剧名', '片名', '专辑', '项目',
                  '系列', '名称', '名字', '标题')
# 「文件名来源」字段 (partname) 的列名线索 —— 决定**每个文件的名字**,
# 那一列的值必须**逐行能区分开**。
PARTNAME_HINTS = ('partname', 'part_name', '分集', '集名', '期数', '话数',
                  'episode', '编号', '集号')

# 文件名里的非法字符 (Windows 那套) —— 一律换成 _
_ILLEGAL_NAME_CHARS = re.compile(r'[\\/:*?"<>|]')

# 认得的图片扩展名 (用来判断 URL 后缀能不能直接用)
_IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tif',
               '.tiff', '.avif'}

# Content-Type → 扩展名
_IMAGE_EXT_BY_CTYPE = {
    'image/jpeg': '.jpg', 'image/jpg': '.jpg', 'image/pjpeg': '.jpg',
    'image/png': '.png', 'image/apng': '.png', 'image/webp': '.webp',
    'image/gif': '.gif', 'image/bmp': '.bmp', 'image/tiff': '.tif',
    'image/avif': '.avif', 'image/svg+xml': '.svg',
}


def _decode_text(data: bytes) -> str:
    """猜文本编码 —— 中文 Excel 导出的 csv 多半是 GBK。"""
    for enc in ('utf-8-sig', 'gbk', 'utf-8'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='replace')


def _read_csv(path: str) -> list[list[str]]:
    with open(path, 'rb') as f:
        data = f.read()
    text = _decode_text(data)
    return [list(row) for row in csv.reader(io.StringIO(text))]


def _local_name(tag: str) -> str:
    """XML 标签的本地名 (去掉 {命名空间})。"""
    return tag.rsplit('}', 1)[-1]


def _column_index(ref: str) -> int | None:
    """单元格引用 → 列号 ('A1' → 0, 'AA1' → 26)。"""
    letters = ''
    for ch in ref:
        if ch.isalpha():
            letters += ch.upper()
        else:
            break
    if not letters:
        return None
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


def _cell_value(cell, shared: list[str]) -> str:
    """一个 xlsx 单元格 → 字符串。"""
    kind = cell.get('t') or 'n'
    if kind == 'inlineStr':
        return ''.join(e.text or '' for e in cell.iter()
                       if _local_name(e.tag) == 't')
    raw = None
    for e in cell.iter():
        if _local_name(e.tag) == 'v':
            raw = e.text or ''
            break
    if kind == 's':                      # sharedStrings 索引
        try:
            return shared[int(raw)]
        except (TypeError, ValueError, IndexError):
            return ''
    if kind == 'b':
        return 'TRUE' if raw == '1' else 'FALSE'
    if kind in ('str', 'e'):             # 公式结果 / 错误值
        return raw or ''
    if not raw:
        return ''
    try:
        num = float(raw)
    except ValueError:
        return raw
    if num.is_integer():                 # 90 别写成 '90.0'
        return str(int(num))
    return str(num)


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    root = ET.fromstring(zf.read('xl/sharedStrings.xml'))
    out: list[str] = []
    for si in root.iter():
        if _local_name(si.tag) != 'si':
            continue
        out.append(''.join(e.text or '' for e in si.iter()
                           if _local_name(e.tag) == 't'))
    return out


def _normalize_target(target: str) -> str:
    """rels 里的 Target → zip 里的路径。"""
    t = (target or '').replace('\\', '/')
    if t.startswith('/'):
        t = t.lstrip('/')
    elif not t.startswith('xl/'):
        t = 'xl/' + t
    parts: list[str] = []
    for part in t.split('/'):
        if part == '..':
            if parts:
                parts.pop()
        elif part not in ('', '.'):
            parts.append(part)
    return '/'.join(parts)


def _first_sheet_path(zf: zipfile.ZipFile, names: set[str]) -> str | None:
    """第一个工作表在 zip 里的路径 (workbook.xml → rels → 目标)。"""
    rid = None
    if 'xl/workbook.xml' in names:
        try:
            root = ET.fromstring(zf.read('xl/workbook.xml'))
        except ET.ParseError:
            root = None
        if root is not None:
            for e in root.iter():
                if _local_name(e.tag) != 'sheet':
                    continue
                for key, value in e.attrib.items():
                    if _local_name(key) == 'id' and value:
                        rid = value
                        break
                if rid:
                    break
    if rid and 'xl/_rels/workbook.xml.rels' in names:
        try:
            rroot = ET.fromstring(zf.read('xl/_rels/workbook.xml.rels'))
        except ET.ParseError:
            rroot = None
        if rroot is not None:
            for e in rroot.iter():
                if _local_name(e.tag) != 'Relationship' or e.get('Id') != rid:
                    continue
                path = _normalize_target(e.get('Target'))
                if path in names:
                    return path
                break
    for name in sorted(names):           # 兜底: 第一个 worksheet
        if name.startswith('xl/worksheets/') and name.endswith('.xml'):
            return name
    return None


def _read_xlsx(path: str) -> list[list[str]]:
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise DocumentFormatError(f'读不了这个 xlsx: {exc}') from exc
    with zf:
        names = set(zf.namelist())
        shared = _read_shared_strings(zf) if 'xl/sharedStrings.xml' in names else []
        sheet_path = _first_sheet_path(zf, names)
        if sheet_path is None:
            raise DocumentFormatError('这个 xlsx 里没有工作表。')
        try:
            root = ET.fromstring(zf.read(sheet_path))
        except ET.ParseError as exc:
            raise DocumentFormatError(f'这个 xlsx 的工作表解析失败: {exc}') from exc
    rows: list[list[str]] = []
    for row_el in root.iter():
        if _local_name(row_el.tag) != 'row':
            continue
        cells: dict[int, str] = {}
        next_idx = 0
        for cell in row_el:
            if _local_name(cell.tag) != 'c':
                continue
            ref = cell.get('r')
            idx = _column_index(ref) if ref else None
            if idx is None:
                idx = next_idx
            cells[idx] = _cell_value(cell, shared)
            next_idx = idx + 1
        width = (max(cells) + 1) if cells else 0
        rows.append([cells.get(i, '') for i in range(width)])
    return rows


def read_tabular_document(path: str, has_header: bool = True) -> TabularDocument:
    """读一份清单文档 (csv / xlsx) → 字段名 + 数据行。

    has_header=False → 第一行**也当数据**, 列名兜底成「字段1、字段2…」。
    """
    if not os.path.isfile(path):
        raise DocumentFormatError(f'文件不存在: {path}')
    ext = os.path.splitext(path)[1].lower()
    if ext == '.csv':
        rows = _read_csv(path)
    elif ext == '.xlsx':
        rows = _read_xlsx(path)
    elif ext == '.xls':
        raise DocumentFormatError(
            '.xls 是旧版二进制格式, 本程序读不了 —— 请用 Excel/WPS '
            '另存为 .xlsx 或 .csv 再试。')
    else:
        raise DocumentFormatError(
            f'不支持的文档格式: {ext or "(无后缀)"} —— 只支持 .csv / .xlsx')
    if has_header and rows:
        headers = [str(c).strip() for c in rows[0]]
        body = rows[1:]
    else:
        width = max((len(r) for r in rows), default=0)
        headers = [f'字段{i + 1}' for i in range(width)]
        body = rows
    width = len(headers)
    body = [[('' if c is None else str(c))
             for c in (list(r[:width]) + [''] * (width - len(r)))]
            for r in body]
    return TabularDocument(headers=headers, rows=body)


# 什么算「像个链接」—— 认海报链接字段时看的就是这个
_URL_PREFIXES = ('http://', 'https://', 'file://', 'ftp://', '//')


def _looks_like_url(value: str) -> bool:
    return str(value).strip().lower().startswith(_URL_PREFIXES)


def guess_link_columns(headers: list[str], rows: list[list[str]]) -> list[int]:
    """猜哪些列是海报链接 —— **可能有多列** (2026-11-04 用户要求)。

    判定:
      1. 列名像链接 (url/link/海报/图片/封面/poster/image…) **且**值里确实
         有链接 → 认
      2. 值像链接 (不看列名) → 认
      3. 一列都不像 → 返回空表 (界面里一个都不勾, 让用户自己挑) ——
         宁可让用户勾, 也不偷偷拿一列错的当链接去下载
    """
    def looks_like_link(col: int) -> bool:
        for row in rows[:20]:
            if col < len(row):
                if _looks_like_url(row[col]):
                    return True
        return False

    by_name = [i for i, header in enumerate(headers)
               if any(k in str(header).strip().lower() for k in POSTER_URL_HINTS)
               and looks_like_link(i)]
    if by_name:
        return by_name
    return [i for i in range(len(headers)) if looks_like_link(i)]


def _column_varies(rows: list[list[str]], col: int) -> bool:
    """这一列的值**逐行能区分开**吗 (不是每行都一样)。"""
    values = {str(row[col]).strip() for row in rows
              if col < len(row) and str(row[col]).strip()}
    return len(values) > 1


def _column_is_text(rows: list[list[str]], col: int) -> bool:
    """这一列的值是**文字**吗 (不是纯数字) —— 数字列 (时长/集数) 当文件名不合适。"""
    values = [str(row[col]).strip() for row in rows
              if col < len(row) and str(row[col]).strip()]
    if not values:
        return False
    for value in values:
        try:
            float(value)
        except ValueError:
            return True          # 有一个不是数字 → 算文字列
    return False


def guess_clipname_column(headers: list[str], rows: list[list[str]]) -> int:
    """猜「命名来源」字段 (clipname) —— 决定**海报地址**(文件夹名)。

    按列名线索 (clipname / 剧名 / 片名 / 专辑…) ; 没有线索就找一列
    **所有行都一样**的 (那多半是片子级字段) ; 都没有 → 第一列。
    """
    for i, header in enumerate(headers):
        low = str(header).strip().lower()
        if any(k in low for k in CLIPNAME_HINTS):
            return i
    for i in range(len(headers)):
        if not _column_varies(rows, i):
            return i
    return 0


def guess_partname_column(headers: list[str], rows: list[list[str]],
                          exclude: int | None = None) -> int:
    """猜「文件名来源」字段 (partname) —— 决定**每个海报文件的名字**。

    要求这一列的值**逐行能区分开** —— 否则所有行会算出同一个文件名,
    后下的把先下的覆盖掉 (2026-11-04 用户踩过这个坑)。
    排除 exclude (通常是「命名来源」那一列, 它是整个片子共用的)，
    也排除**链接列** (拿链接当文件名会得到一串网址)。
    """
    link_cols = set(guess_link_columns(headers, rows))
    for i, header in enumerate(headers):
        if i == exclude or i in link_cols:
            continue
        low = str(header).strip().lower()
        if any(k in low for k in PARTNAME_HINTS) and _column_varies(rows, i):
            return i
    for i in range(len(headers)):
        if i == exclude or i in link_cols:
            continue
        if _column_varies(rows, i) and _column_is_text(rows, i):
            return i
    for i in range(len(headers)):
        if i == exclude or i in link_cols:
            continue
        if _column_varies(rows, i):
            return i
    if exclude is not None and 0 <= exclude < len(headers):
        return exclude
    return 0


def render_filename(template: str, partname: str, imgcol: str) -> str:
    """按命名规则渲染出一个文件名 (不含扩展名)。

    模板里 {partname} / [partname] → 命名来源字段的值,
    {imgcol} / [imgcol] → 海报链接字段的**字段名**。
    非法字符 (\\ / : * ? " < > |) 一律换成 _。
    """
    name = str(template)
    for token, value in (('{partname}', partname), ('{imgcol}', imgcol),
                         ('[partname]', partname), ('[imgcol]', imgcol)):
        name = name.replace(token, str(value))
    name = _ILLEGAL_NAME_CHARS.sub('_', name)
    name = re.sub(r'\s+', ' ', name).strip().strip('.')
    if len(name) > 120:
        name = name[:120].rstrip('. ')
    return name or 'poster'


def extension_for_url(url: str, content_type: str = '') -> str:
    """海报的扩展名: URL 后缀 → Content-Type → 兜底 .jpg。"""
    path = urllib.parse.urlsplit(url).path
    ext = os.path.splitext(path)[1].lower()
    if ext in _IMAGE_EXTS:
        return ext
    ctype = (content_type or '').split(';')[0].strip().lower()
    if ctype in _IMAGE_EXT_BY_CTYPE:
        return _IMAGE_EXT_BY_CTYPE[ctype]
    return '.jpg'


def poster_default_dir(doc_path: str, partname: str) -> str:
    """海报地址的默认值: 文档所在目录/<partname>-poster。"""
    folder = os.path.dirname(os.path.abspath(doc_path))
    name = _ILLEGAL_NAME_CHARS.sub('_', str(partname).strip()).strip('. ')
    return os.path.join(folder, (name or 'poster') + '-poster')


def build_poster_jobs(rows: list[list[str]], part_index: int,
                      links: list[tuple[int, str]], template: str
                      ) -> list[tuple[str, str]]:
    """表格 → 一串 (链接, 文件名)。

    links: [(列号, 列名), ...] —— **可以有多个链接字段**: 每一行 × 每一个
    链接字段 = 一个任务, 文件名里的 {imgcol} 取**该列的列名**。
    链接为空的格子跳过。
    """
    jobs: list[tuple[str, str]] = []
    for row in rows:
        part = str(row[part_index]).strip() if 0 <= part_index < len(row) else ''
        for col_index, col_name in links:
            if col_index >= len(row):
                continue
            url = str(row[col_index]).strip()
            if not url:
                continue
            jobs.append((url, render_filename(template, part, col_name)))
    return jobs


def fetch_bytes(url: str, timeout: float = 30.0) -> tuple[bytes, str]:
    """抓一个链接 → (内容, Content-Type)。失败抛异常。"""
    req = urllib.request.Request(
        url, headers={'User-Agent': 'vdcheck-poster/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        try:
            ctype = resp.headers.get('Content-Type', '') or ''
        except Exception:
            ctype = ''
    return data, ctype


def unique_poster_path(output_dir: str, base_name: str, ext: str,
                       taken: set[str] | None = None) -> str:
    """挑一个**没被占用**的落盘路径: `名字.ext`、`名字_1.ext`、`名字_2.ext`…

    被占 = 磁盘上已经有这个文件, **或**本批次已经用过这个名字
    (taken —— 并行下载时另外一条线程刚占过)。占到的名字会**记进
    taken**, 所以调用方必须**持着同一把锁**调它 (check + 占位不是
    原子的, 不加锁两条线程会同时看中同一个名字)。

    按顺序找**第一个空位** —— 不会出现「a_1 → a_1_1」那种链式后缀
    (app 里移动文件那个老毛病, 见产品文档 §6 #23)。
    """
    index = 0
    while True:
        name = (f'{base_name}{ext}' if index == 0
                else f'{base_name}_{index}{ext}')
        path = os.path.join(output_dir, name)
        free = (taken is None or name not in taken)
        if free and not os.path.exists(path):
            if taken is not None:
                taken.add(name)
            return path
        index += 1


def download_poster(url: str, output_dir: str, base_name: str,
                    timeout: float = 30.0, taken: set[str] | None = None,
                    lock=None) -> str:
    """下载一张海报到 output_dir → 落盘路径。

    扩展名按 URL 后缀 / Content-Type 定; 目录不存在会**建**;
    **重名自动加后缀** (名字、名字_1、名字_2…) —— 磁盘上已有的、
    或本批次已占用的，都让开。

    taken / lock: 并行下载时共用 —— 只有「挑名字」那一步在锁里,
    **网络下载在锁外** (不然并行就没意义了)。
    """
    data, ctype = fetch_bytes(url, timeout=timeout)
    ext = extension_for_url(url, ctype)
    os.makedirs(output_dir, exist_ok=True)
    if lock is None:
        dest = unique_poster_path(output_dir, base_name, ext, taken)
    else:
        with lock:
            dest = unique_poster_path(output_dir, base_name, ext, taken)
    with open(dest, 'wb') as f:
        f.write(data)
    return dest
