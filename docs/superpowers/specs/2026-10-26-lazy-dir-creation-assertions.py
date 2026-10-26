"""snapshot / checked 目录按需创建 — 一次性断言脚本。

需求:
    扫描 / 浏览时**不建**任何目录; 只有真的要用那个目录了才建:
      - 弹窗点「开始截图」→ 建 <截图地址> 目录 (默认 <文件路径>\\snapshot)
      - 点「移动达标文件 / 移动全部文件」→ 建 <迁移地址> 目录 (默认 <文件路径>\\checked)
    「浏览」= 选**父目录**, 自动套一层固定子目录:
      - 截图浏览选 F → 最终输出 F\\snapshot
      - 迁移浏览选 F → 最终目标 F\\checked

跑之前是坏的 (已知会失败的点):
    - `_sync_derived_paths` 里有一句 `os.makedirs(snap_default)`, 扫描就建目录
    - 两处「浏览」直接把所选文件夹当最终目录, 没套子目录

运行: python docs/superpowers/specs/2026-10-26-lazy-dir-creation-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

from video_checker import filedialog, messagebox  # noqa: E402  (被 monkeypatch 的目标)
from video_checker import VideoCheckerApp, VideoInfo  # noqa: E402


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
    return os.path.join(directory, "checked").replace('/', '\\')


def snap_expect(directory: str) -> str:
    """截图地址的期望值: 只在 win32 转反斜杠 (macOS 反斜杠路径 os.path.isdir 不认)。"""
    p = os.path.join(directory, 'snapshot')
    return p.replace('/', '\\') if sys.platform == 'win32' else p


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
    from tkinterdnd2 import TkinterDnD
    # 必须用 TkinterDnD.Tk(): VideoGridView.bind_drop 会调 drop_target_register,
    # 普通 tk.Tk() 上会 AttributeError。
    root = TkinterDnD.Tk()
    root.withdraw()
    app = VideoCheckerApp(root)
    root.update_idletasks()

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
          app.dest_var.get() == dest_expect(src),
          f"got={app.dest_var.get()!r} want={dest_expect(src)!r}")
    check("截图地址字段仍填了默认值",
          app.snapshot_dest_var.get() == snap_expect(src),
          f"got={app.snapshot_dest_var.get()!r} want={snap_expect(src)!r}")

    section("浏览文件夹也不建目录")
    real_opendir = filedialog.askdirectory
    filedialog.askdirectory = lambda **_kw: src
    try:
        app._browse_path()
    finally:
        filedialog.askdirectory = real_opendir
    check("浏览后源目录下没有 snapshot",
          not os.path.isdir(os.path.join(src, 'snapshot')),
          f"path={os.path.join(src, 'snapshot')!r}")
    check("浏览后源目录下没有 checked",
          not os.path.isdir(os.path.join(src, 'checked')),
          f"path={os.path.join(src, 'checked')!r}")

    # ================= 2. 「浏览」套一层子目录 =================
    section("迁移地址「浏览」套一层 checked")
    picked = tempfile.mkdtemp(prefix='vdcheck_pick_')
    filedialog.askdirectory = lambda **_kw: picked
    try:
        app._browse_dest()
    finally:
        filedialog.askdirectory = real_opendir
    check("迁移地址 = 所选文件夹\\checked",
          app.dest_var.get() == dest_expect(picked),
          f"got={app.dest_var.get()!r} want={dest_expect(picked)!r}")
    check("浏览迁移地址时不建目录",
          not os.path.isdir(dest_expect(picked)),
          f"path={dest_expect(picked)!r}")

    section("截图地址「浏览」套一层 snapshot")
    from video_checker import SnapshotProgressDialog
    dlg = SnapshotProgressDialog(root, [], os.path.join(src, 'snapshot'))
    root.update_idletasks()
    filedialog.askdirectory = lambda **_kw: picked
    try:
        dlg._browse_output_dir()
    finally:
        filedialog.askdirectory = real_opendir
    check("截图地址 = 所选文件夹\\snapshot",
          dlg._output_dir_var.get() == snap_expect(picked),
          f"got={dlg._output_dir_var.get()!r} want={snap_expect(picked)!r}")
    check("浏览截图地址时不建目录",
          not os.path.isdir(snap_expect(picked)),
          f"path={snap_expect(picked)!r}")

    # ================= 3. 点「开始截图」才建 =================
    section("点「开始截图」才建 snapshot 目录")
    # 用一个空文件列表的弹窗: makedirs 发生在起 worker 之前, worker 没活干,
    # 不会去抽帧。目录建没建得到这里就见分晓。
    snap_dir = os.path.join(picked, 'snapshot')
    dlg2 = SnapshotProgressDialog(root, [], snap_dir)
    root.update_idletasks()
    check("点之前目录不存在",
          not os.path.isdir(snap_dir), f"path={snap_dir!r}")
    dlg2._on_action_btn()          # 等价于点「开始截图」
    root.update_idletasks()
    check("点之后目录建出来了",
          os.path.isdir(snap_dir), f"path={snap_dir!r}")
    dlg2._on_close()
    dlg.top.destroy()

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
        app.video_results.append(
            make_info(title='a.mp4', rel_path='./a.mp4', full_path=video,
                      is_passing=True))
        app.dest_var.set(dest_dir)
        check("点之前目录不存在",
              not os.path.isdir(dest_dir), f"path={dest_dir!r}")

        real_askyesno = messagebox.askyesno
        real_showinfo = messagebox.showinfo
        messagebox.askyesno = lambda *_a, **_kw: True
        messagebox.showinfo = lambda *_a, **_kw: None
        try:
            app._move_passing_files()
        finally:
            messagebox.askyesno = real_askyesno
            messagebox.showinfo = real_showinfo
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
        app.video_results.append(
            make_info(title='b.mp4', rel_path='./b.mp4', full_path=video2,
                      is_passing=False))
        app.dest_var.set(dest_dir2)
        messagebox.askyesno = lambda *_a, **_kw: True
        messagebox.showinfo = lambda *_a, **_kw: None
        try:
            app._move_all_files()
        finally:
            messagebox.askyesno = real_askyesno
            messagebox.showinfo = real_showinfo
        check("点之后目录建出来了",
              os.path.isdir(dest_dir2), f"path={dest_dir2!r}")
    finally:
        os.chdir(cwd)

    root.destroy()
    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
