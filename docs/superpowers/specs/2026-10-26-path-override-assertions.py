"""手动改「文件路径」后立即生效 — 一次性断言脚本。

背景 bug:
    拖拽加载文件后, `_selected_files` 记着那批文件, `_start_scan` 里
    `if self._selected_files:` 优先用它 —— 「文件路径」框里手打的目录
    被完全忽略。以前必须先点「清除记录」才生效。

改法:
    路径框被写入 (用户手动改) → 丢弃「特定文件列表」模式, 下次开始检测
    按路径框里的目录扫整个文件夹。浏览选文件夹本来就是这个行为。

跑之前是坏的 (2026-10-26 修):
    手动改路径后 `_selected_files` 仍是拖入的那批 → 开始检测扫的还是老文件

运行: python docs/superpowers/specs/2026-10-26-path-override-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import sys
import tempfile

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


def make_info(title='a.mp4') -> VideoInfo:
    return VideoInfo(
        rel_path='./', title=title, resolution='1920x1080', frame_rate='25.00',
        bitrate_kbps=32000.0, video_codec='h264', audio_codec='aac',
        audio_channels='2', audio_sample_rate='48.0', duration='00:01:00',
        file_size='1.0 GB', is_passing=True, sample_rate_passing=True,
        bitrate_std=30000.0, sample_rate_std=48.0,
        full_path=f'/x/{title}', container_format='mp4',
    )


class FakeDropEvent:
    def __init__(self, data: str):
        self.data = data


def stub_scan(app, calls):
    """把两个 worker 换成记录器, 免得真起线程调 ffprobe。"""
    app._scan_worker = lambda d, r, b, s: calls.append(('dir', d))
    app._scan_files_worker = lambda f, b, s: calls.append(('files', tuple(f)))
    app._check_queue = lambda: None


def drop(app, files):
    """模拟拖拽。stub 的 worker 不会发 done, scanning 会一直 True,
    下一次拖拽/检测会弹模态框卡住 —— 这里先复位。"""
    app.scanning = False
    app._on_file_drop(FakeDropEvent(' '.join(files)))


def scan(app):
    """模拟点「开始检测」。同样先复位 scanning。"""
    app.scanning = False
    app._start_scan()
    app.scanning = False


def main() -> int:
    from tkinterdnd2 import TkinterDnD
    from video_checker import VideoCheckerApp
    # 必须用 TkinterDnD.Tk(): VideoGridView.bind_drop 会调 drop_target_register,
    # 普通 tk.Tk() 上会 AttributeError。
    root = TkinterDnD.Tk()
    root.geometry('1280x700')
    app = VideoCheckerApp(root)
    root.update_idletasks()

    calls: list = []
    stub_scan(app, calls)

    drop_dir = tempfile.mkdtemp(prefix='vdcheck_drop_')
    other_dir = tempfile.mkdtemp(prefix='vdcheck_other_')
    drop_files = [os.path.join(drop_dir, 'a.mp4'),
                  os.path.join(drop_dir, 'b.mp4')]

    section("拖拽加载文件")
    drop(app, drop_files)
    root.update_idletasks()
    check("_selected_files 记下了拖入的文件",
          app._selected_files == drop_files, f"got={app._selected_files!r}")
    check("path_var 填成了拖入文件的所在目录",
          app.path_var.get().strip().replace('\\', '/') == drop_dir,
          f"got={app.path_var.get()!r}")

    section("手动改路径后立即生效 (不用先清除记录)")
    app.path_var.set(other_dir)          # 模拟用户手动输入
    root.update_idletasks()
    check("改路径后 _selected_files 被丢弃",
          app._selected_files is None, f"got={app._selected_files!r}")

    calls.clear()
    app.path_var.set(other_dir)
    scan(app)
    root.update_idletasks()
    check("开始检测扫的是新路径 (整个目录)",
          calls == [('dir', other_dir)], f"got={calls!r}")

    section("拖拽后不碰路径框 → 仍然扫拖入的那批文件")
    drop(app, drop_files)
    root.update_idletasks()
    check("拖拽后 _selected_files 又有了",
          app._selected_files == drop_files, f"got={app._selected_files!r}")
    # 这里用 _scan_target 而不是 _start_scan: 拖拽把路径框填成反斜杠形式,
    # macOS 上 os.path.isdir 不认那个路径 → _start_scan 会弹模态框卡住。
    kind, payload = app._scan_target(other_dir)
    check("扫描目标仍是拖入的文件列表",
          kind == 'files' and tuple(payload) == tuple(drop_files),
          f"kind={kind!r} payload={payload!r}")

    section("浏览选文件夹仍然清空 (不能回归)")
    drop(app, drop_files)
    root.update_idletasks()
    check("先拖拽建立了文件列表模式",
          app._selected_files is not None)
    app._selected_files = None            # 浏览路径的既有行为
    root.update_idletasks()
    check("浏览后 _selected_files 为空", app._selected_files is None)
    check("浏览后扫描目标是整个目录",
          app._scan_target(other_dir) == ('dir', other_dir),
          f"got={app._scan_target(other_dir)!r}")

    section("拖拽进来的文件本身仍按文件扫 (不受影响)")
    drop(app, drop_files)
    root.update_idletasks()
    calls.clear()
    kind, payload = app._scan_target(other_dir)
    check("扫描目标仍是拖入的文件",
          kind == 'files' and tuple(payload) == tuple(drop_files),
          f"kind={kind!r} payload={payload!r}")

    root.destroy()
    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
