"""snapshot / checked 目录按需创建 — 一次性断言脚本。

需求:
    扫描 / 浏览时**不建**任何目录; 只有真的要用那个目录了才建:
      - 真要抽帧时 → 建 <截图地址> 目录 (默认 <文件路径>\\snapshot)
      - 点「移动达标文件 / 移动全部文件」→ 建 <迁移地址> 目录
        (默认 <文件路径>\\checked)
    「浏览」= 选**父目录**, 自动套一层固定子目录:
      - 截图浏览选 F → 最终输出 F\\snapshot
      - 迁移浏览选 F → 最终目标 F\\checked

跑之前是坏的 (已知会失败的点):
    - 派生路径的填默认值逻辑里有一句 `os.makedirs(snap_default)`, 扫描就建目录
    - 两处「浏览」直接把所选文件夹当最终目录, 没套子目录

跑法: python docs/superpowers/specs/2026-10-26-lazy-dir-creation-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

# Qt 版主窗口要 QApplication 才能建 (QFontMetrics 没有 QGuiApplication 会
# 直接 abort 进程), 所以先建 app 再 import video_qt。没有显示环境时用
# offscreen 平台。
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

from video_core import VideoInfo  # noqa: E402
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


def dest_expect(directory: str) -> str:
    """迁移地址的期望值: 迁移地址一律转反斜杠 (既有约定)。"""
    return checked_dest_for(directory)


def snap_expect(directory: str) -> str:
    """截图地址的期望值: 只在 win32 转反斜杠 (macOS 反斜杠路径 os.path.isdir 不认)。"""
    return snapshot_dest_for(directory)


def make_info(title='a.mp4', rel_path='./', full_path='/x/a.mp4',
              is_passing=True) -> VideoInfo:
    return VideoInfo(
        rel_path=rel_path, title=title, resolution='1920x1080',
        frame_rate='25.00', bitrate_kbps=32000.0, video_codec='h264',
        audio_codec='aac', audio_channels='2', audio_sample_rate='48.0',
        duration='00:01:00', file_size='1.0 GB', is_passing=is_passing,
        sample_rate_passing=True, bitrate_std=30000.0, sample_rate_std=48.0,
        full_path=full_path, container_format='mp4',
    )


def main() -> int:
    app = MainWindow()

    # ================= 1. 扫描/浏览阶段一个目录都不许建 =================
    section("扫描阶段不建目录")
    src = tempfile.mkdtemp(prefix='vdcheck_src_')
    app._sync_derived_paths(src)
    check("源目录下没有 snapshot",
          not os.path.isdir(os.path.join(src, 'snapshot')),
          f"path={os.path.join(src, 'snapshot')!r}")
    check("源目录下没有 checked",
          not os.path.isdir(os.path.join(src, 'checked')),
          f"path={os.path.join(src, 'checked')!r}")
    check("迁移地址字段仍填了默认值",
          app.dest_edit.text() == dest_expect(src),
          f"got={app.dest_edit.text()!r} want={dest_expect(src)!r}")
    check("截图地址字段仍填了默认值",
          app.snapshot_dest == snap_expect(src),
          f"got={app.snapshot_dest!r} want={snap_expect(src)!r}")

    section("浏览文件夹也不建目录")
    app._ask_directory = lambda title: src
    app._browse_path()
    check("浏览后源目录下没有 snapshot",
          not os.path.isdir(os.path.join(src, 'snapshot')),
          f"path={os.path.join(src, 'snapshot')!r}")
    check("浏览后源目录下没有 checked",
          not os.path.isdir(os.path.join(src, 'checked')),
          f"path={os.path.join(src, 'checked')!r}")

    # ================= 2. 「浏览」套一层子目录 =================
    section("迁移地址「浏览」套一层 checked")
    picked = tempfile.mkdtemp(prefix='vdcheck_pick_')
    app._ask_directory = lambda title: picked
    app._browse_dest()
    check("迁移地址 = 所选文件夹\\checked",
          app.dest_edit.text() == dest_expect(picked),
          f"got={app.dest_edit.text()!r} want={dest_expect(picked)!r}")
    check("浏览迁移地址时不建目录",
          not os.path.isdir(dest_expect(picked)),
          f"path={dest_expect(picked)!r}")

    section("截图地址「浏览」套一层 snapshot")
    app._browse_snapshot_dest()
    check("截图地址 = 所选文件夹\\snapshot",
          app.snapshot_dest == snap_expect(picked),
          f"got={app.snapshot_dest!r} want={snap_expect(picked)!r}")
    check("浏览截图地址时不建目录",
          not os.path.isdir(snap_expect(picked)),
          f"path={snap_expect(picked)!r}")

    # ================= 3. 真要用截图目录时才建 =================
    section("ensure_snapshot_dir() 才建 snapshot 目录")
    # 这一节验的是**建目录的那个口子** (ensure_snapshot_dir), 不是
    # 「点『开始截图』按钮」—— 那个按钮在定点截图弹窗里, 是 Task 6 的
    # 活儿。按钮 → 这个口子的接线由 Task 6 验 (qt-main 里那条
    # 「截图目录只由 ensure_snapshot_dir() 建」是给它立的规矩)。
    snap_dir = snap_expect(picked)
    app.snapshot_dest = snap_dir
    check("建之前目录不存在",
          not os.path.isdir(snap_dir), f"path={snap_dir!r}")
    app.ensure_snapshot_dir()
    check("建之后目录建出来了",
          os.path.isdir(snap_dir), f"path={snap_dir!r}")

    # ================= 4. 点「移动」才建 checked =================
    # 迁移地址一律转反斜杠, macOS 上 os.makedirs 会把 '\var\...\checked'
    # 当成一个**字面目录名**在 CWD 里建出来 → 把 CWD 挪进沙箱, 别在仓库
    # 根目录留垃圾。Windows 上反斜杠就是分隔符, 沙箱对它是空操作。
    sandbox = tempfile.mkdtemp(prefix='vdcheck_sandbox_')
    cwd = os.getcwd()
    os.chdir(sandbox)
    try:
        section("点「移动达标文件」才建 checked 目录")
        mov_src = tempfile.mkdtemp(prefix='vdcheck_mov_')
        video = os.path.join(mov_src, 'a.mp4')
        with open(video, 'wb') as f:
            f.write(b'fake')
        dest_parent = tempfile.mkdtemp(prefix='vdcheck_dest_')
        dest_dir = dest_expect(dest_parent)
        app.video_results.clear()
        app._add_result(
            make_info(title='a.mp4', rel_path='./a.mp4', full_path=video,
                      is_passing=True))
        app.dest_edit.setText(dest_dir)
        check("点之前目录不存在",
              not os.path.isdir(dest_dir), f"path={dest_dir!r}")

        app._ask_yes_no = lambda text: True
        app._show_move_result = lambda text: None
        app._move_passing_files()
        check("点之后目录建出来了",
              os.path.isdir(dest_dir), f"path={dest_dir!r}")
        check("文件真的移到里面了",
              os.path.isfile(os.path.join(dest_dir, 'a.mp4')),
              f"dest={dest_dir!r}")

        # 移动全部文件同理
        section("点「移动全部文件」才建 checked 目录")
        mov_src2 = tempfile.mkdtemp(prefix='vdcheck_mov2_')
        video2 = os.path.join(mov_src2, 'b.mp4')
        with open(video2, 'wb') as f:
            f.write(b'fake')
        dest_parent2 = tempfile.mkdtemp(prefix='vdcheck_dest2_')
        dest_dir2 = dest_expect(dest_parent2)
        app.video_results.clear()
        app._add_result(
            make_info(title='b.mp4', rel_path='./b.mp4', full_path=video2,
                      is_passing=False))
        app.dest_edit.setText(dest_dir2)
        app._move_all_files()
        check("点之后目录建出来了",
              os.path.isdir(dest_dir2), f"path={dest_dir2!r}")
    finally:
        os.chdir(cwd)

    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
