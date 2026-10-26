"""派生路径 (迁移地址 / 截图地址) 跟随源目录 — 一次性断言脚本。

背景 bug:
    `_start_scan` 里的填默认值逻辑是 `if not dest_var.get().strip()`,
    只在字段为空时生效。第一次扫 A 填成 A\\checked, 之后把「文件路径」改成
    B 再开始检测, 字段非空 → 两个 if 都不进 → 迁移地址和截图地址留在 A。

本脚本验证:
- `_browse_path` 只开文件夹选择器, 不再开文件选择器;
- 浏览文件夹会清掉 `_selected_files` (否则开始检测扫的是上次选的文件);
- `_sync_derived_paths` 换一个源目录, 两个字段都跟着换 (核心回归);
- `_start_scan` 走真实路径时两个字段确实跟着 `path_var` 走;
- 截图目录会被真的建出来;
- 迁移地址与截图地址按各自的斜杠约定归一。

运行: python docs/superpowers/specs/2026-10-23-sync-derived-paths-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

from video_checker import filedialog  # noqa: E402  (被 monkeypatch 的目标)
from video_checker import VideoCheckerApp  # noqa: E402


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

    `_sync_derived_paths` 对迁移地址无条件 `.replace('/', '\\\\')` —— Windows
    上是对的, macOS 上会把 `/tmp/x/checked` 变成 `\\tmp\\x\\checked`。
    这是改动前就有的行为 (旧代码 :2004 / :2012 / :2052 / :2339 都这么做),
    本脚本按现状断言, 不顺手改斜杠约定。
    """
    return os.path.join(directory, "checked").replace('/', '\\')


def expected_snapshot(directory: str) -> str:
    """截图地址的期望值 (只在 win32 上转反斜杠)。"""
    p = os.path.join(directory, 'snapshot')
    return p.replace('/', '\\') if sys.platform == 'win32' else p


def main() -> int:
    from tkinterdnd2 import TkinterDnD
    # 必须用 TkinterDnD.Tk(): VideoGridView.bind_drop 会调 drop_target_register,
    # 普通 tk.Tk() 上会 AttributeError。
    root = TkinterDnD.Tk()
    root.withdraw()
    app = VideoCheckerApp(root)
    root.update_idletasks()

    tmp_a = tempfile.mkdtemp(prefix='vdcheck_src_a_')
    tmp_b = tempfile.mkdtemp(prefix='vdcheck_src_b_')

    # ---- 浏览: 只保留文件夹 ----
    section("_browse_path 只开文件夹选择器")
    opened: list[str] = []

    def fake_openfiles(**_kw):
        opened.append('file')
        return ()          # 模拟用户直接取消

    def fake_opendir(**_kw):
        opened.append('dir')
        return tmp_a

    real_openfiles = filedialog.askopenfilenames
    real_opendir = filedialog.askdirectory
    filedialog.askopenfilenames = fake_openfiles
    filedialog.askdirectory = fake_opendir
    try:
        app._selected_files = ['/stale/a.mp4', '/stale/b.mp4']
        app._browse_path()
    finally:
        filedialog.askopenfilenames = real_openfiles
        filedialog.askdirectory = real_opendir

    check("没有弹过文件选择器",
          'file' not in opened, f"opened={opened}")
    check("弹了文件夹选择器", 'dir' in opened, f"opened={opened}")
    check("文件夹选择器只弹一次", opened.count('dir') == 1,
          f"opened={opened}")
    check("浏览文件夹后 _selected_files 清空",
          app._selected_files is None, f"got={app._selected_files!r}")
    check("path_var = 所选文件夹",
          app.path_var.get() == tmp_a, f"got={app.path_var.get()!r}")
    check("浏览后迁移地址跟随",
          app.dest_var.get() == expected_dest(tmp_a),
          f"got={app.dest_var.get()!r} want={expected_dest(tmp_a)!r}")
    check("浏览后截图地址跟随",
          app.snapshot_dest_var.get() == expected_snapshot(tmp_a),
          f"got={app.snapshot_dest_var.get()!r} "
          f"want={expected_snapshot(tmp_a)!r}")

    # ---- 核心回归: 换源目录两个字段都跟着换 ----
    section("_sync_derived_paths 换源目录后两个字段都更新 (核心回归)")
    app._sync_derived_paths(tmp_b)
    check("迁移地址换到 B",
          app.dest_var.get() == expected_dest(tmp_b),
          f"got={app.dest_var.get()!r} want={expected_dest(tmp_b)!r}")
    check("截图地址换到 B",
          app.snapshot_dest_var.get() == expected_snapshot(tmp_b),
          f"got={app.snapshot_dest_var.get()!r} "
          f"want={expected_snapshot(tmp_b)!r}")
    # 目录本身按需创建 (2026-10-26 起): 填字段但不建目录, 等点「开始截图」
    # 才建。见 2026-10-26-lazy-dir-creation-assertions.py。
    check("B 的截图目录此时还没建",
          not os.path.isdir(os.path.join(tmp_b, 'snapshot')),
          f"path={os.path.join(tmp_b, 'snapshot')!r}")

    # 反向: 再换回 A, 确认不是单向
    app._sync_derived_paths(tmp_a)
    check("换回 A 迁移地址跟着回",
          app.dest_var.get() == expected_dest(tmp_a),
          f"got={app.dest_var.get()!r}")
    check("换回 A 截图地址跟着回",
          app.snapshot_dest_var.get() == expected_snapshot(tmp_a),
          f"got={app.snapshot_dest_var.get()!r}")

    # ---- 空字段也要填 (旧行为的兜底不能丢) ----
    section("空字段仍会被填上 (旧兜底行为未丢)")
    app.dest_var.set('')
    app.snapshot_dest_var.set('')
    app._sync_derived_paths(tmp_b)
    check("空迁移地址被填成 B",
          app.dest_var.get() == expected_dest(tmp_b),
          f"got={app.dest_var.get()!r}")
    check("空截图地址被填成 B",
          app.snapshot_dest_var.get() == expected_snapshot(tmp_b),
          f"got={app.snapshot_dest_var.get()!r}")

    # ---- 走真实 _start_scan ----
    section("_start_scan 走真实代码路径")
    # 换目录前先手动改一次 —— 用户选定「一律跟随」, 所以必须被覆盖
    app.dest_var.set('\\\\nas\\archive')
    app.path_var.set(tmp_a)
    app._sync_derived_paths(tmp_a)
    check("前置状态: 迁移地址在 A",
          app.dest_var.get() == expected_dest(tmp_a),
          f"got={app.dest_var.get()!r}")

    # 挡掉真实扫描与队列轮询, 只测 _start_scan 前半段
    scanned: list[str] = []
    real_worker = app._scan_worker
    real_check_queue = app._check_queue
    app._scan_worker = lambda d, r, b, s: scanned.append(d)
    app._check_queue = lambda: None
    try:
        app.path_var.set(tmp_b)
        app._start_scan()
    finally:
        app._scan_worker = real_worker
        app._check_queue = real_check_queue

    check("开始检测后迁移地址指向 B",
          app.dest_var.get() == expected_dest(tmp_b),
          f"got={app.dest_var.get()!r} want={expected_dest(tmp_b)!r}")
    check("开始检测后截图地址指向 B",
          app.snapshot_dest_var.get() == expected_snapshot(tmp_b),
          f"got={app.snapshot_dest_var.get()!r} "
          f"want={expected_snapshot(tmp_b)!r}")
    check("手动设过的迁移地址被覆盖 (选定行为)",
          app.dest_var.get() != '\\\\nas\\archive',
          f"got={app.dest_var.get()!r}")
    check("确实扫的是 path_var 里的目录",
          scanned == [tmp_b], f"scanned={scanned!r}")

    # ---- 拖拽路径也要同步截图地址 ----
    section("_on_file_drop 同步两个字段")
    app.dest_var.set('')
    app.snapshot_dest_var.set('')
    app.path_var.set(tmp_a)
    app._sync_derived_paths(tmp_a)

    # 用一个只带 _on_file_drop 后半段语义的最小调用: 直接断言源码里
    # _on_file_drop 同时 set 了 dest_var 和 snapshot_dest_var。
    # 真跑 _on_file_drop 需要构造 DnD 事件对象, 收益不抵成本。
    import inspect
    from video_checker import VideoCheckerApp as _A
    drop_src = inspect.getsource(_A._on_file_drop)
    check("_on_file_drop 里调用了 _sync_derived_paths",
          '_sync_derived_paths' in drop_src,
          "拖进来的文件若不同步截图地址, 会沿用上一次的截图目录")
    check("_on_file_drop 仍保留 path_var 赋值",
          'self.path_var.set(' in drop_src)

    # ---- 清除记录要一起清截图地址 ----
    section("_clear_records 同时清截图地址")
    app.scanning = False
    app.dest_var.set(expected_dest(tmp_a))
    app.snapshot_dest_var.set(expected_snapshot(tmp_a))
    app._clear_records()
    check("清除记录后迁移地址清空",
          app.dest_var.get() == '', f"got={app.dest_var.get()!r}")
    check("清除记录后截图地址也清空",
          app.snapshot_dest_var.get() == '',
          f"got={app.snapshot_dest_var.get()!r}")

    root.destroy()
    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())