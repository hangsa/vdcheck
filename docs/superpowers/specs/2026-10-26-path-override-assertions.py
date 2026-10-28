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
import atexit
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))


_tmpdirs: list[str] = []


def mkdtemp(prefix: str = 'vdcheck_') -> str:
    """建临时目录, 记下来, 退出时清掉。

    断言脚本建了一堆 vdcheck_* 目录又不删 —— 用户的 $TMPDIR 会被塞满。
    """
    path = tempfile.mkdtemp(prefix=prefix)
    _tmpdirs.append(path)
    return path


def cleanup_tmpdirs() -> None:
    for path in _tmpdirs:
        shutil.rmtree(path, ignore_errors=True)
    _tmpdirs.clear()


atexit.register(cleanup_tmpdirs)

# Qt 版主窗口要 QApplication 才能建 (QFontMetrics 没有 QGuiApplication 会
# 直接 abort 进程), 所以先建 app 再 import video_qt。没有显示环境时用
# offscreen 平台。
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

from video_qt import MainWindow  # noqa: E402


_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "✓" if condition else "✗"
    line = f"  {mark} {name}" + (f" — {detail}" if detail else "")
    print(line)
    if not condition:
        _failures.append(line)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def stub_scan(app, calls):
    """把真正开扫描的那一步换成记录器, 免得真起线程调 ffprobe。"""
    app._begin_scan = (lambda kind, payload, bitrate_std, sample_rate_std:
                       calls.append((kind, payload)))


def drop(app, files):
    """模拟拖拽。stub 掉的 _begin_scan 不会真去扫。"""
    app._on_file_drop(files)


def scan(app):
    """模拟点「开始检测」。"""
    app._start_scan()


def main() -> int:
    app = MainWindow()

    calls: list = []
    stub_scan(app, calls)

    drop_dir = mkdtemp(prefix='vdcheck_drop_')
    other_dir = mkdtemp(prefix='vdcheck_other_')
    drop_files = [os.path.join(drop_dir, 'a.mp4'),
                  os.path.join(drop_dir, 'b.mp4')]
    for f in drop_files:
        open(f, 'w').close()

    section("拖拽加载文件")
    drop(app, drop_files)
    check("_selected_files 记下了拖入的文件",
          app._selected_files == drop_files, f"got={app._selected_files!r}")
    check("路径框填成了拖入文件的所在目录",
          app.path_edit.text().strip().replace('\\', '/') == drop_dir,
          f"got={app.path_edit.text()!r}")

    section("手动改路径后立即生效 (不用先清除记录)")
    app.path_edit.setText(other_dir)          # 模拟用户手动输入
    check("改路径后 _selected_files 被丢弃",
          app._selected_files is None, f"got={app._selected_files!r}")

    calls.clear()
    app.path_edit.setText(other_dir)
    scan(app)
    check("开始检测扫的是新路径 (整个目录)",
          calls == [('dir', other_dir)], f"got={calls!r}")

    section("拖拽后不碰路径框 → 仍然扫拖入的那批文件")
    drop(app, drop_files)
    check("拖拽后 _selected_files 又有了",
          app._selected_files == drop_files, f"got={app._selected_files!r}")
    kind, payload = app._scan_target(other_dir)
    check("扫描目标仍是拖入的文件列表",
          kind == 'files' and tuple(payload) == tuple(drop_files),
          f"kind={kind!r} payload={payload!r}")

    section("浏览选文件夹仍然清空 (不能回归)")
    drop(app, drop_files)
    check("先拖拽建立了文件列表模式",
          app._selected_files is not None)
    app._selected_files = None            # 浏览路径的既有行为
    check("浏览后 _selected_files 为空", app._selected_files is None)
    check("浏览后扫描目标是整个目录",
          app._scan_target(other_dir) == ('dir', other_dir),
          f"got={app._scan_target(other_dir)!r}")

    section("拖拽进来的文件本身仍按文件扫 (不受影响)")
    drop(app, drop_files)
    calls.clear()
    kind, payload = app._scan_target(other_dir)
    check("扫描目标仍是拖入的文件",
          kind == 'files' and tuple(payload) == tuple(drop_files),
          f"kind={kind!r} payload={payload!r}")

    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
