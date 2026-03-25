"""视频码率检查器 - 检测视频文件码率是否达标"""

import json
import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from dataclasses import dataclass
from tkinter import filedialog, messagebox, ttk

# 视频扩展名集合
VIDEO_EXTENSIONS = {
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.ts', '.m2ts',
    '.mpg', '.mpeg', '.webm', '.3gp', '.m4v', '.vob', '.ogv',
    '.rm', '.rmvb', '.asf', '.divx', '.f4v', '.mxf', '.mts',
    '.m1v', '.m2v', '.mpv', '.qt', '.y4m', '.nut', '.dv',
    '.tp', '.trp', '.hevc', '.h264', '.h265', '.264', '.265',
}

DEFAULT_BITRATE_KBPS = 30000


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
    duration: str
    file_size: str
    is_passing: bool
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
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE

        result = subprocess.run(
            [
                'ffprobe', '-v', 'quiet',
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
    data: dict, full_path: str, base_path: str, bitrate_std: float
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

    is_passing = bitrate_kbps >= bitrate_std

    return VideoInfo(
        rel_path=rel_path,
        title=title,
        resolution=resolution,
        frame_rate=frame_rate,
        bitrate_kbps=bitrate_kbps,
        video_codec=video_codec,
        audio_codec=audio_codec,
        audio_channels=audio_channels,
        duration=duration,
        file_size=file_size,
        is_passing=is_passing,
        full_path=full_path,
    )


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
        self.root.title("视频码率检查器")
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

        # === Row 2: 码率标准 ===
        bitrate_frame = ttk.Frame(self.root, padding=5)
        bitrate_frame.pack(fill='x')

        ttk.Label(bitrate_frame, text="码率标准:").pack(side='left')
        self.bitrate_var = tk.StringVar(value=str(DEFAULT_BITRATE_KBPS))
        self.bitrate_entry = ttk.Entry(bitrate_frame, textvariable=self.bitrate_var, width=10)
        self.bitrate_entry.pack(side='left', padx=(5, 2))
        ttk.Label(bitrate_frame, text="kbps").pack(side='left')

        # === Main: 结果表格 ===
        tree_frame = ttk.Frame(self.root)
        tree_frame.pack(fill='both', expand=True, padx=5, pady=5)

        columns = (
            'rel_path', 'title', 'resolution', 'frame_rate', 'bitrate',
            'video_codec', 'audio_codec', 'audio_channels', 'duration',
            'file_size', 'result',
        )
        self.tree = ttk.Treeview(tree_frame, columns=columns, show='headings', selectmode='extended')

        headers = {
            'rel_path': ('文件夹', 200),
            'title': ('标题', 150),
            'resolution': ('分辨率', 90),
            'frame_rate': ('帧率', 80),
            'bitrate': ('码率(kbps)', 90),
            'video_codec': ('视频编码', 80),
            'audio_codec': ('音频编码', 80),
            'audio_channels': ('声道数', 55),
            'duration': ('时长', 75),
            'file_size': ('文件大小', 80),
            'result': ('结果', 70),
        }

        for col, (heading, width) in headers.items():
            self.tree.heading(col, text=heading)
            anchor = 'center'
            if col in ('rel_path', 'title'):
                anchor = 'w'
            elif col in ('bitrate', 'file_size'):
                anchor = 'e'
            self.tree.column(col, width=width, anchor=anchor)

        # 滚动条
        vsb = ttk.Scrollbar(tree_frame, orient='vertical', command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        # 颜色标记
        self.tree.tag_configure('pass', foreground='#228B22')
        self.tree.tag_configure('fail', foreground='#DC143C')

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

    # ---- 事件处理 ----

    def _browse_path(self):
        folder = filedialog.askdirectory(title="选择视频文件夹")
        if folder:
            self.path_var.set(folder)
            self.dest_var.set(os.path.join(folder, "Passed"))

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

        # 设置默认目标路径
        if not self.dest_var.get().strip():
            self.dest_var.set(os.path.join(directory, "Passed"))

        self.scanning = True
        self.scan_btn.config(state='disabled')
        self.video_results.clear()
        # 清空表格
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.result_queue = queue.Queue()

        t = threading.Thread(
            target=self._scan_worker,
            args=(directory, self.recursive_var.get(), bitrate_std),
            daemon=True,
        )
        t.start()
        self._check_queue()

    def _scan_worker(self, directory: str, recursive: bool, bitrate_std: float):
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
            info = parse_video_info(data, fp, directory, bitrate_std)
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
        bitrate_display = f"{info.bitrate_kbps:.0f}"
        tag = 'pass' if info.is_passing else 'fail'
        result_text = "达标" if info.is_passing else "不达标"

        self.tree.insert('', 'end', values=(
            info.rel_path,
            info.title,
            info.resolution,
            info.frame_rate,
            bitrate_display,
            info.video_codec,
            info.audio_codec,
            info.audio_channels,
            info.duration,
            info.file_size,
            result_text,
        ), tags=(tag,))

    def _scan_complete(self):
        """扫描完成处理"""
        self.scanning = False
        self.scan_btn.config(state='normal')

        total = len(self.video_results)
        passed = sum(1 for v in self.video_results if v.is_passing)
        failed = total - passed
        self.status_var.set(f"检测完成：共 {total} 个视频文件，{passed} 个达标，{failed} 个不达标")

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


def main():
    root = tk.Tk()
    VideoCheckerApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
