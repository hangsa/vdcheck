"""派生路径 (迁移地址 / 截图地址) 跟随源目录 — 一次性断言脚本。

背景 bug:
    `_start_scan` 里的填默认值逻辑是 `if not dest_var.get().strip()`,
    只在字段为空时生效。第一次扫 A 填成 A\\checked, 之后把「文件路径」改成
    B 再开始检测, 字段非空 → 两个 if 都不进 → 迁移地址和截图地址留在 A。

本脚本验证 (Qt 版主窗口 video_qt.MainWindow):
- 浏览只选文件夹 (不选文件);
- 浏览文件夹会清掉「拖拽留下的文件列表」 (否则开始检测扫的是上次选的文件);
- 派生路径换一个源目录, 两个字段都跟着换 (核心回归);
- 开始检测走真实路径时两个字段确实跟着路径框走;
- 截图目录**不会被建出来** (按需创建, 见 2026-10-26-lazy-dir-creation);
- 迁移地址与截图地址按各自的斜杠约定归一。

运行: python docs/superpowers/specs/2026-10-23-sync-derived-paths-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import inspect
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

from video_qt import (  # noqa: E402
    MainWindow, checked_dest_for, snapshot_dest_for,
)


_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "✓" if condition else "✗"
    line = f"  {mark} {name}" + (f" — {detail}" if detail else "")
    print(line)
    if not condition:
        _failures.append(line)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def expected_dest(directory: str) -> str:
    """迁移地址的期望值。

    派生路径对迁移地址**无条件** `.replace('/', '\\\\')` —— Windows 上是对的,
    macOS 上会把 `/tmp/x/checked` 变成 `\\tmp\\x\\checked`。
    这是改动前就有的行为, 本脚本按现状断言, 不顺手改斜杠约定。
    """
    return checked_dest_for(directory)


def expected_snapshot(directory: str) -> str:
    """截图地址的期望值 (只在 win32 上转反斜杠)。"""
    return snapshot_dest_for(directory)


def main() -> int:
    app = MainWindow()

    tmp_a = mkdtemp(prefix='vdcheck_src_a_')
    tmp_b = mkdtemp(prefix='vdcheck_src_b_')

    # ---- 浏览: 只保留文件夹 ----
    section("浏览只开文件夹选择器")
    opened: list[str] = []

    def fake_ask_dir(title):
        opened.append(title)
        return tmp_a

    app._ask_directory = fake_ask_dir
    app._selected_files = ['/stale/a.mp4', '/stale/b.mp4']
    app._browse_path()

    check("只弹了一次对话框", len(opened) == 1, f"opened={opened}")
    browse_src = inspect.getsource(MainWindow._browse_path)
    check("没有用到任何「选文件」的对话框 API",
          not any(api in browse_src for api in
                  ('getOpenFileName', 'getOpenFileNames', 'getExistingFile')),
          "选文件只能拖拽 (产品文档 §6 #7)")

    check("浏览文件夹后「拖拽留下的文件列表」清空",
          app._selected_files is None, f"got={app._selected_files!r}")
    check("路径框 = 所选文件夹",
          app.path_edit.text() == tmp_a, f"got={app.path_edit.text()!r}")
    check("浏览后迁移地址跟随",
          app.dest_edit.text() == expected_dest(tmp_a),
          f"got={app.dest_edit.text()!r} want={expected_dest(tmp_a)!r}")
    check("浏览后截图地址跟随",
          app.snapshot_dest == expected_snapshot(tmp_a),
          f"got={app.snapshot_dest!r} want={expected_snapshot(tmp_a)!r}")

    # ---- 核心回归: 换源目录两个字段都跟着换 ----
    section("派生路径换源目录后两个字段都更新 (核心回归)")
    app._sync_derived_paths(tmp_b)
    check("迁移地址换到 B",
          app.dest_edit.text() == expected_dest(tmp_b),
          f"got={app.dest_edit.text()!r} want={expected_dest(tmp_b)!r}")
    check("截图地址换到 B",
          app.snapshot_dest == expected_snapshot(tmp_b),
          f"got={app.snapshot_dest!r} want={expected_snapshot(tmp_b)!r}")
    # 目录本身按需创建 (2026-10-26 起): 填字段但不建目录, 等点「开始截图」
    # 才建。见 2026-10-26-lazy-dir-creation-assertions.py。
    check("B 的截图目录此时还没建",
          not os.path.isdir(os.path.join(tmp_b, 'snapshot')),
          f"path={os.path.join(tmp_b, 'snapshot')!r}")

    # 反向: 再换回 A, 确认不是单向
    app._sync_derived_paths(tmp_a)
    check("换回 A 迁移地址跟着回",
          app.dest_edit.text() == expected_dest(tmp_a),
          f"got={app.dest_edit.text()!r}")
    check("换回 A 截图地址跟着回",
          app.snapshot_dest == expected_snapshot(tmp_a),
          f"got={app.snapshot_dest!r}")

    # ---- 空字段也要填 (旧行为的兜底不能丢) ----
    section("空字段仍会被填上 (旧兜底行为未丢)")
    app.dest_edit.setText('')
    app.snapshot_dest = ''
    app._sync_derived_paths(tmp_b)
    check("空迁移地址被填成 B",
          app.dest_edit.text() == expected_dest(tmp_b),
          f"got={app.dest_edit.text()!r}")
    check("空截图地址被填成 B",
          app.snapshot_dest == expected_snapshot(tmp_b),
          f"got={app.snapshot_dest!r}")

    # ---- 走真实 _start_scan ----
    section("开始检测走真实代码路径")
    # 换目录前先手动改一次 —— 用户选定「一律跟随」, 所以必须被覆盖
    app.dest_edit.setText('\\\\nas\\archive')
    app.path_edit.setText(tmp_a)
    app._sync_derived_paths(tmp_a)
    check("前置状态: 迁移地址在 A",
          app.dest_edit.text() == expected_dest(tmp_a),
          f"got={app.dest_edit.text()!r}")

    # 挡掉真实扫描 (会起线程调 ffprobe), 只测开始检测的前半段
    scanned: list[tuple] = []
    real_begin = app._begin_scan
    app._begin_scan = (lambda kind, payload, bitrate_std, sample_rate_std:
                       scanned.append((kind, payload)))
    try:
        app.path_edit.setText(tmp_b)
        app._start_scan()
    finally:
        app._begin_scan = real_begin

    check("开始检测后迁移地址指向 B",
          app.dest_edit.text() == expected_dest(tmp_b),
          f"got={app.dest_edit.text()!r} want={expected_dest(tmp_b)!r}")
    check("开始检测后截图地址指向 B",
          app.snapshot_dest == expected_snapshot(tmp_b),
          f"got={app.snapshot_dest!r} want={expected_snapshot(tmp_b)!r}")
    check("手动设过的迁移地址被覆盖 (选定行为)",
          app.dest_edit.text() != '\\\\nas\\archive',
          f"got={app.dest_edit.text()!r}")
    check("确实扫的是路径框里的目录",
          scanned == [('dir', tmp_b)], f"scanned={scanned!r}")

    # ---- 拖拽路径也要同步截图地址 ----
    section("拖拽同步两个字段")
    app.dest_edit.setText('')
    app.snapshot_dest = ''
    app.path_edit.setText(tmp_a)
    app._sync_derived_paths(tmp_a)

    # 真跑一次拖拽 (挡掉扫描): 拖进来的文件所在目录要同步到两个字段。
    dropped = os.path.join(tmp_b, 'a.mp4')
    open(dropped, 'w').close()
    real_begin = app._begin_scan
    app._begin_scan = lambda *args, **kwargs: None
    try:
        app._on_file_drop([dropped])
    finally:
        app._begin_scan = real_begin

    check("拖入后迁移地址跟着拖入文件的目录",
          app.dest_edit.text() == expected_dest(tmp_b),
          f"got={app.dest_edit.text()!r} want={expected_dest(tmp_b)!r}")
    check("拖入后截图地址跟着拖入文件的目录",
          app.snapshot_dest == expected_snapshot(tmp_b),
          f"got={app.snapshot_dest!r} want={expected_snapshot(tmp_b)!r}")
    check("拖入后路径框填的是文件所在目录",
          app.path_edit.text().replace('\\', '/') == tmp_b,
          f"got={app.path_edit.text()!r}")

    # ---- 清除记录要一起清截图地址 ----
    section("清除记录同时清截图地址")
    app.scanning = False
    app.dest_edit.setText(expected_dest(tmp_a))
    app.snapshot_dest = expected_snapshot(tmp_a)
    app._clear_records()
    check("清除记录后迁移地址清空",
          app.dest_edit.text() == '', f"got={app.dest_edit.text()!r}")
    check("清除记录后截图地址也清空",
          app.snapshot_dest == '', f"got={app.snapshot_dest!r}")

    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
