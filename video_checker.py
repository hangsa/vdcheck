"""视频码率检查器 —— 入口。

界面实现在 video_qt.py (PySide6); 纯逻辑在 video_core.py。
这里重新导出 core 的名字, 让既有的断言脚本
(`from video_checker import X`) 不用改。

Tk 版 (VideoGridView / Tooltip / EnlargeDialog / SnapshotProgressDialog /
VideoCheckerApp) 已随 Task 7 整体删除 —— 本文件**不再** import tkinter /
tkinterdnd2。
"""
import video_core  # noqa: F401
from video_core import (  # noqa: F401
    DEFAULT_BITRATE_KBPS, DEFAULT_SAMPLE_RATE_KHZ, EXTRACT_THUMB_W, FG_FAIL,
    FG_PASS, VIDEO_EXTENSIONS, VideoInfo, _SORTABLE_COLUMNS, _cell_fg,
    _extract_sort_key, _header_text_with_arrow, _next_sort_state,
    extract_frame, format_duration, format_file_size, format_title,
    get_ffmpeg_path, get_ffprobe_path, parse_threshold, parse_time_token,
    parse_video_info, run_ffprobe, scan_video_files, time_token_to_string,
    truncate_text,
)


def main() -> None:
    """启动 Qt 版界面。

    `video_qt` 的 import 放在函数里: 断言脚本 `import video_checker` 时
    不必把 PySide6 拉进来 (更不必建 QApplication)。
    """
    from video_qt import main as qt_main
    qt_main()


if __name__ == '__main__':
    main()
