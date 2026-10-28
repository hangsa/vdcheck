"""Qt 主窗口的行为断言。

对照 docs/产品文档.md §3.1~3.5 / §4.1~4.4 与
docs/superpowers/plans/2026-10-27-pyside6-qtableview.md 的 Task 5
(含「Qt 特有的行为对齐清单」8 条与「列宽两个方向」的接线要求)。

跑法:
    QT_QPA_PLATFORM=offscreen python docs/superpowers/specs/2026-10-27-qt-main-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

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

# 没有显示环境时 Qt 用 offscreen 平台 (会打一条字体族警告到 stderr, 不在范围)。
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QDragEnterEvent, QDropEvent, QFont, QFontMetrics,
)
from PySide6.QtWidgets import QApplication, QHeaderView  # noqa: E402

# QApplication 必须**先于** video_qt 建好: ui_font() 惰性求值时会拷贝当前
# app 的默认字体, app 还不存在时字体族是空的 (family=''), 字宽度量整套
# 偏掉, QFontMetrics(那个字体) 直接 abort 进程。
app = QApplication.instance() or QApplication(sys.argv)

import video_qt  # noqa: E402
from video_core import VideoInfo  # noqa: E402
from video_qt import (  # noqa: E402
    VIDEO_COLUMNS, MainWindow, VideoTableModel, checked_dest_for,
    snapshot_dest_for, ui_font,
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


def make_info(title='a.mp4', full_path='/x/a.mp4', passing=True,
              bitrate=32000.0) -> VideoInfo:
    return VideoInfo(
        rel_path='./', title=title, resolution='1920x1080',
        frame_rate='30.00', bitrate_kbps=bitrate, video_codec='h264',
        audio_codec='aac', audio_channels='2', audio_sample_rate='48.0',
        duration='00:01:00', file_size='1.0 GB', is_passing=passing,
        sample_rate_passing=True, bitrate_std=30000.0, sample_rate_std=48.0,
        full_path=full_path, container_format='mp4',
        frame_rate_fps=30.0, sample_rate_khz=48.0)


def fake_ffprobe_json() -> dict:
    """一段合法的 ffprobe JSON (不真的调 ffprobe)。"""
    return {
        "streams": [
            {"codec_type": "video", "codec_name": "h264",
             "width": 1920, "height": 1080, "r_frame_rate": "30/1",
             "bit_rate": "32000000"},
            {"codec_type": "audio", "codec_name": "aac",
             "channels": 2, "sample_rate": "48000"},
        ],
        "format": {"duration": "120.0", "size": "100000000",
                   "format_name": "mp4"},
    }


def pump(seconds: float = 0.05) -> None:
    """泵事件循环, 让 worker 线程发来的 queued 信号送达主线程。"""
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_scan(win: MainWindow, timeout: float = 15.0) -> bool:
    """等一轮扫描结束 (worker 在另一个线程里跑)。"""
    end = time.time() + timeout
    while win.scanning and time.time() < end:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    return not win.scanning


def first_ink(img, x0: int, x1: int):
    """一段横向区间里第一列有墨迹的位置 (相对 x0)。找不到返回 None。"""
    for x in range(max(0, x0), min(img.width(), x1)):
        for y in range(img.height()):
            if img.pixelColor(x, y).lightness() < 120:
                return x - x0
    return None


def main() -> int:
    # ---- 假的 ffprobe: 不真的调外部程序 ----
    real_run_ffprobe = video_qt.run_ffprobe
    video_qt.run_ffprobe = lambda path: fake_ffprobe_json()

    try:
        return _run()
    finally:
        video_qt.run_ffprobe = real_run_ffprobe
        cleanup_tmpdirs()


def _run() -> int:
    # ================================================================
    section("字体: 必须在 QApplication 之后求值 (清单 #1 / #2)")
    font = ui_font()
    check("ui_font() 的字体族非空 (app 已存在)",
          bool(font.family()), f"got={font.family()!r}")
    check("字号 9pt", font.pointSize() == 9, f"got={font.pointSize()}")
    check("QFontMetrics 量得动 (没有 abort)",
          QFontMetrics(ui_font()).horizontalAdvance('码率(kbps)') > 0)
    video_qt.apply_app_font(app)
    check("app.setFont(ui_font()) 已生效", app.font() == ui_font(),
          f"app={app.font()!r}")
    check("app 的字号是 9pt", app.font().pointSize() == 9,
          f"got={app.font().pointSize()}")
    # MainWindow 自己也要 apply —— 直接 MainWindow() (断言脚本、将来的嵌入
    # 用法) 拿到的必须是同一套字体, 否则模型按 9pt 量宽、视图按 app 默认
    # 字体渲染, 预截断好的文字会溢出列宽 (清单 #2)。
    app.setFont(QFont('Courier', 14))          # 明显不是 ui_font()
    check("前置: app 字体被换成别的", app.font() != ui_font(),
          f"got={app.font()!r}")
    win_font = MainWindow()
    check("MainWindow 自己就把 app 字体设回 ui_font() (不用先调 main())",
          app.font() == ui_font(), f"got={app.font()!r}")

    # app 之前取字体 → 明确报错, 不是 abort (退出码 134) 也不是悄悄绑一个
    # 用不了的字体。用子进程验 —— 本进程里 app 已经在了。
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
    proc = subprocess.run(
        [sys.executable, '-c',
         'import video_qt; video_qt.ui_font()'],
        capture_output=True, text=True, cwd=repo_root)
    check("app 之前取字体 → 明确报错 (不是 abort / 退出码 134)",
          proc.returncode not in (134, -6)
          and 'QApplication' in (proc.stdout + proc.stderr),
          f"rc={proc.returncode} out={proc.stdout[-120:]!r} "
          f"err={proc.stderr[-120:]!r}")

    # ================================================================
    section("状态栏文案序列")
    src = mkdtemp(prefix='vdcheck_qt_src_')
    for name in ('a.mp4', 'b.mp4', 'c.mp4'):
        open(os.path.join(src, name), 'w').close()

    win = MainWindow()
    win.show()
    app.processEvents()
    check("初始文案是「就绪」",
          win.status_bar.currentMessage() == '就绪',
          f"got={win.status_bar.currentMessage()!r}")

    statuses: list[str] = []
    win.status_changed.connect(statuses.append)
    win.path_edit.setText(src)
    win._start_scan()
    check("扫描中按钮变「检测中」", win.scan_btn.text() == '检测中',
          f"got={win.scan_btn.text()!r}")
    check("扫描中按钮**仍可点** (点即取消)", win.scan_btn.isEnabled())
    ok = wait_scan(win)
    check("扫描正常结束", ok)
    check("终态文案「检测完成：共 3 · 达标 3 · 不达标 0」",
          win.status_bar.currentMessage()
          == '检测完成：共 3 · 达标 3 · 不达标 0',
          f"got={win.status_bar.currentMessage()!r}")
    check("终态用**全角冒号**",
          '：' in win.status_bar.currentMessage())
    check("终态用间隔号 · (U+00B7)",
          ' · ' in win.status_bar.currentMessage())
    check("状态序列里有「正在扫描文件列表...」",
          '正在扫描文件列表...' in statuses, f"got={statuses}")
    progress = [s for s in statuses if s.startswith('正在检测... ')]
    check("每个文件一条进度 (3 条)", len(progress) == 3, f"got={progress}")
    check("进度文案格式「正在检测... (i/N) 文件名」",
          all(re.fullmatch(r'正在检测\.\.\. \(\d+/3\) \S+', s)
              for s in progress), f"got={progress}")
    check("进度序号从 1 到 3",
          [int(re.search(r'\((\d+)/', s).group(1)) for s in progress]
          == [1, 2, 3], f"got={progress}")
    check("扫描后表格有 3 行", win.model.rowCount() == 3,
          f"got={win.model.rowCount()}")
    check("扫描后结果列表有 3 条", len(win.video_results) == 3)

    # ---- 中途取消 ----
    section("状态栏文案: 中途停止")
    win2 = MainWindow()
    statuses2: list[str] = []
    win2.status_changed.connect(statuses2.append)
    calls = {'n': 0}

    def cancel_ffprobe(path):
        calls['n'] += 1
        if calls['n'] == 1:
            win2._cancel.set()          # 第一个文件处理到一半时按下取消
        return fake_ffprobe_json()

    video_qt.run_ffprobe = cancel_ffprobe
    win2.path_edit.setText(src)
    win2._start_scan()
    ok = wait_scan(win2)
    check("取消后扫描结束", ok)
    check("停止文案「已停止检测（已完成 1/3）：共 1 · 达标 1 · 不达标 0」",
          win2.status_bar.currentMessage()
          == '已停止检测（已完成 1/3）：共 1 · 达标 1 · 不达标 0',
          f"got={win2.status_bar.currentMessage()!r}")
    check("停止文案**只有一个冒号**",
          win2.status_bar.currentMessage().count('：') == 1,
          f"got={win2.status_bar.currentMessage()!r}")
    check("停止后按钮恢复「开始检测」",
          win2.scan_btn.text() == '开始检测' and win2.scan_btn.isEnabled(),
          f"got={win2.scan_btn.text()!r} enabled={win2.scan_btn.isEnabled()}")

    # ---- 按钮三态 ----
    section("按钮状态机: 开始检测 → 检测中 → 正在停止... → 开始检测")
    win3 = MainWindow()
    video_qt.run_ffprobe = lambda path: (time.sleep(0.2),
                                         fake_ffprobe_json())[1]
    win3.path_edit.setText(src)
    win3._start_scan()
    pump(0.05)
    check("扫描中: 文案「检测中」", win3.scan_btn.text() == '检测中',
          f"got={win3.scan_btn.text()!r}")
    check("扫描中: 仍可点", win3.scan_btn.isEnabled())
    win3._on_scan_btn_click()            # 再点一次 = 取消
    check("取消瞬间: 文案「正在停止...」",
          win3.scan_btn.text() == '正在停止...',
          f"got={win3.scan_btn.text()!r}")
    check("取消瞬间: 按钮禁用", not win3.scan_btn.isEnabled())
    ok = wait_scan(win3)
    check("停止后恢复「开始检测」",
          win3.scan_btn.text() == '开始检测' and win3.scan_btn.isEnabled(),
          f"got={win3.scan_btn.text()!r} enabled={win3.scan_btn.isEnabled()}")
    video_qt.run_ffprobe = lambda path: fake_ffprobe_json()

    # ================================================================
    section("派生路径 (迁移地址 / 截图地址)")
    win4 = MainWindow()
    src_a = mkdtemp(prefix='vdcheck_qt_a_')
    src_b = mkdtemp(prefix='vdcheck_qt_b_')
    win4._sync_derived_paths(src_a)
    check("迁移地址 = <源>\\checked (无条件转反斜杠)",
          win4.dest_edit.text() == checked_dest_for(src_a),
          f"got={win4.dest_edit.text()!r} want={checked_dest_for(src_a)!r}")
    check("截图地址 = <源>/snapshot (只在 win32 转反斜杠)",
          win4.snapshot_dest == snapshot_dest_for(src_a),
          f"got={win4.snapshot_dest!r} want={snapshot_dest_for(src_a)!r}")
    check("派生路径**不建目录** (snapshot)",
          not os.path.isdir(os.path.join(src_a, 'snapshot')))
    check("派生路径**不建目录** (checked)",
          not os.path.isdir(os.path.join(src_a, 'checked')))

    win4._sync_derived_paths(src_b)
    check("换源目录: 迁移地址跟着换",
          win4.dest_edit.text() == checked_dest_for(src_b),
          f"got={win4.dest_edit.text()!r}")
    check("换源目录: 截图地址跟着换",
          win4.snapshot_dest == snapshot_dest_for(src_b),
          f"got={win4.snapshot_dest!r}")
    win4._sync_derived_paths(src_a)
    check("换回来: 迁移地址跟着回",
          win4.dest_edit.text() == checked_dest_for(src_a),
          f"got={win4.dest_edit.text()!r}")
    check("换回来: 截图地址跟着回",
          win4.snapshot_dest == snapshot_dest_for(src_a),
          f"got={win4.snapshot_dest!r}")

    win4.dest_edit.setText('\\\\nas\\archive')
    win4._sync_derived_paths(src_b)
    check("用户手填的迁移地址被**无条件覆盖**",
          win4.dest_edit.text() == checked_dest_for(src_b),
          f"got={win4.dest_edit.text()!r}")

    # ================================================================
    section("「浏览」语义: 选的是父目录, 下面套一层子目录")
    picked = mkdtemp(prefix='vdcheck_qt_pick_')
    win4._ask_directory = lambda title: picked
    win4._selected_files = ['/stale/a.mp4', '/stale/b.mp4']
    win4._browse_path()
    check("路径框 = 所选文件夹", win4.path_edit.text() == picked,
          f"got={win4.path_edit.text()!r}")
    check("浏览清掉拖拽留下的文件列表", win4._selected_files is None,
          f"got={win4._selected_files!r}")
    check("浏览后迁移地址跟随",
          win4.dest_edit.text() == checked_dest_for(picked),
          f"got={win4.dest_edit.text()!r}")
    check("浏览后截图地址跟随",
          win4.snapshot_dest == snapshot_dest_for(picked),
          f"got={win4.snapshot_dest!r}")
    check("浏览不建目录", not os.path.isdir(checked_dest_for(picked)))

    win4._browse_dest()
    check("迁移地址「浏览」= 父目录 + checked",
          win4.dest_edit.text() == checked_dest_for(picked),
          f"got={win4.dest_edit.text()!r}")
    win4._browse_snapshot_dest()
    check("截图地址「浏览」= 父目录 + snapshot",
          win4.snapshot_dest == snapshot_dest_for(picked),
          f"got={win4.snapshot_dest!r}")
    check("浏览截图地址也不建目录", not os.path.isdir(snapshot_dest_for(picked)))

    # ================================================================
    section("清除记录")
    win5 = MainWindow()
    win5.bitrate_edit.setText('12345')
    win5.sample_rate_edit.setText('44')
    win5._add_result(make_info('a.mp4', '/x/a.mp4'))
    win5.dest_edit.setText('D:\\dest')
    win5.snapshot_dest = '/x/snapshot'
    win5._selected_files = ['/x/a.mp4']
    win5._clear_records()
    check("结果列表清空", win5.video_results == [], f"got={win5.video_results!r}")
    check("表格清空", win5.model.rowCount() == 0)
    check("迁移地址清空", win5.dest_edit.text() == '',
          f"got={win5.dest_edit.text()!r}")
    check("截图地址清空", win5.snapshot_dest == '',
          f"got={win5.snapshot_dest!r}")
    check("拖拽留下的文件列表清空", win5._selected_files is None)
    check("阈值**不清**",
          win5.bitrate_edit.text() == '12345'
          and win5.sample_rate_edit.text() == '44',
          f"got={win5.bitrate_edit.text()!r}/{win5.sample_rate_edit.text()!r}")
    check("状态文案「已清除记录：共 0 · 达标 0 · 不达标 0」",
          win5.status_bar.currentMessage()
          == '已清除记录：共 0 · 达标 0 · 不达标 0',
          f"got={win5.status_bar.currentMessage()!r}")

    infos: list[str] = []
    win5._show_info = lambda text: infos.append(text)
    win5.scanning = True
    win5._clear_records()
    check("扫描中清除被拒绝",
          infos == ['正在检测中，请等待完成后再清除。'], f"got={infos!r}")
    win5.scanning = False

    # ================================================================
    section("移动文件")
    # 迁移地址一律转反斜杠, macOS 上 os.makedirs 会把 '\var\...\checked'
    # 当成一个**字面目录名**在 CWD 里建出来 → 把 CWD 挪进沙箱, 别在仓库
    # 根目录留垃圾。Windows 上反斜杠就是分隔符, 沙箱对它是空操作。
    sandbox = mkdtemp(prefix='vdcheck_qt_sandbox_')
    cwd = os.getcwd()
    os.chdir(sandbox)
    try:
        win6 = MainWindow()
        confirms: list[str] = []
        results: list[str] = []
        win6._ask_yes_no = lambda text: (confirms.append(text), True)[1]
        win6._show_move_result = lambda text: results.append(text)
        mov_src = mkdtemp(prefix='vdcheck_qt_mov_')
        paths = []
        for name in ('a.mp4', 'b.mp4', 'c.mp4', 'd.mp4'):
            p = os.path.join(mov_src, name)
            open(p, 'w').close()
            paths.append(p)
        # 故意乱序插入 —— 证明移动按**插入序**而不是排序后的显示序。
        # d.mp4 故意**不达标**: 「移动全部文件」要连不达标的文件一起搬,
        # 没有这个 fixture 的话两个按钮在断言里区分不出来。
        win6._add_result(make_info('c.mp4', paths[2], bitrate=1000.0))
        win6._add_result(make_info('a.mp4', paths[0], bitrate=50000.0))
        win6._add_result(make_info('b.mp4', paths[1], bitrate=20000.0))
        win6._add_result(make_info('d.mp4', paths[3], passing=False))
        dest_parent = mkdtemp(prefix='vdcheck_qt_dest_')
        dest = checked_dest_for(dest_parent)
        win6.dest_edit.setText(dest)
        win6.model.on_header_clicked(4)      # 按码率排序 (显示序 ≠ 插入序)
        app.processEvents()
        display_order = [win6.model.data(win6.model.index(r, 1),
                                         Qt.DisplayRole) for r in range(3)]
        check("表格显示序 ≠ 插入序 (前提成立)",
              display_order != ['./c.mp4', './a.mp4', './b.mp4'],
              f"got={display_order}")

        moved_order: list[str] = []
        real_move = video_qt.shutil.move
        video_qt.shutil.move = lambda s, d: moved_order.append(s)
        try:
            win6._move_passing_files()
        finally:
            video_qt.shutil.move = real_move
        check("确认框文案逐字照搬 (只算达标的 3 个)",
              confirms == [f"确定要将 3 个达标文件移动到:\n{dest}\n？"],
              f"got={confirms!r}")
        check("移动顺序 = **插入序** (不是排序后的显示序)",
              moved_order == [paths[2], paths[0], paths[1]],
              f"got={moved_order!r}")
        check("不达标的文件没被「移动达标文件」带走",
              paths[3] not in moved_order, f"got={moved_order!r}")
        check("结果框文案「成功移动 3 个文件。」",
              results == ['成功移动 3 个文件。'], f"got={results!r}")

        # ---- 「移动全部文件」: 连不达标的文件一起搬 ----
        confirms.clear()
        results.clear()
        moved_order.clear()
        video_qt.shutil.move = lambda s, d: moved_order.append(s)
        try:
            win6._move_all_files()
        finally:
            video_qt.shutil.move = real_move
        check("移动全部的确认框文案 (4 个, 含不达标的)",
              confirms == [f"确定要将 4 个文件移动到:\n{dest}\n？"],
              f"got={confirms!r}")
        check("移动全部把**不达标的**文件也带走了",
              paths[3] in moved_order, f"got={moved_order!r}")

        # ---- 没有达标文件 / 没有文件 ----
        win6.video_results.clear()
        win6._add_result(make_info('x.mp4', '/x/x.mp4', passing=False))
        infos.clear()
        win6._show_info = lambda text: infos.append(text)
        win6._move_passing_files()
        check("没有达标文件 → 拒绝",
              infos == ['没有达标文件需要移动。'], f"got={infos!r}")
        infos.clear()
        win6.video_results.clear()
        win6._move_all_files()
        check("没有文件 → 拒绝", infos == ['没有文件需要移动。'], f"got={infos!r}")

        # ---- 失败清单最多 20 条 ----
        win7 = MainWindow()
        results7: list[str] = []
        win7._show_move_result = lambda text: results7.append(text)
        win7._ask_yes_no = lambda text: True
        win7.dest_edit.setText(dest)
        for i in range(25):
            win7._add_result(make_info(f'f{i}.mp4', f'/nonexistent/f{i}.mp4'))
        win7._move_passing_files()
        text7 = results7[0]
        check("失败总数照实报 (25 个)", '25 个文件移动失败' in text7,
              f"got={text7[:80]!r}")
        tail7 = text7.partition('个文件移动失败:\n')[2]
        check("失败清单**最多 20 条**",
              len(tail7.strip().split('\n')) == 20,
              f"got={len(tail7.strip().split(chr(10)))}")
        check("成功数是 0", text7.startswith('成功移动 0 个文件。'),
              f"got={text7[:40]!r}")

        # ---- 目标重名 → _1 去重 ----
        dup_dir = mkdtemp(prefix='vdcheck_qt_dup_')
        open(os.path.join(dup_dir, 'a.mp4'), 'w').close()
        dup_src = os.path.join(mov_src, 'a.mp4')
        check("目标已有同名 → _1",
              MainWindow._get_unique_dest(dup_src, dup_dir)
              == os.path.join(dup_dir, 'a_1.mp4'),
              f"got={MainWindow._get_unique_dest(dup_src, dup_dir)!r}")
    finally:
        os.chdir(cwd)

    # ================================================================
    section("拖拽")
    win8 = MainWindow()
    infos8: list[str] = []
    win8._show_info = lambda text: infos8.append(text)
    win8._show_error = lambda text: infos8.append(text)
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile('/tmp/dropdir/a.mp4'),
                QUrl.fromLocalFile('/tmp/dropdir/b.txt')])
    drag_ev = QDragEnterEvent(QPointF(10, 10), Qt.CopyAction, md,
                               Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(win8.view.viewport(), drag_ev)
    check("拖入事件被接受 (含文件的 URL)", drag_ev.isAccepted())
    drop_ev = QDropEvent(QPointF(10, 10), Qt.CopyAction, md,
                          Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(win8.view.viewport(), drop_ev)
    check("拖入后**立即开始扫描** (不等点「开始检测」)", win8.scanning)
    ok = wait_scan(win8)
    check("拖入扫描正常结束", ok)
    check("只保留视频扩展名的文件",
          win8._selected_files == ['/tmp/dropdir/a.mp4'],
          f"got={win8._selected_files!r}")
    check("路径框填的是拖入文件所在目录",
          win8.path_edit.text().replace('\\', '/') == '/tmp/dropdir',
          f"got={win8.path_edit.text()!r}")
    check("迁移地址跟随拖入目录",
          win8.dest_edit.text() == checked_dest_for('/tmp/dropdir'),
          f"got={win8.dest_edit.text()!r}")

    infos8.clear()
    win8.scanning = True
    win8._on_file_drop(['/tmp/x.mp4'])
    check("扫描中拖入被拒绝",
          infos8 == ['正在检测中，请等待完成后再添加文件。'],
          f"got={infos8!r}")
    win8.scanning = False

    infos8.clear()
    win8._on_file_drop(['/tmp/x.txt', '/tmp/y.zip'])
    check("全是非视频扩展名 → 拒绝",
          infos8 == ['没有找到视频文件。'], f"got={infos8!r}")

    infos8.clear()
    win8._on_file_drop(['/tmp/dropdir'])          # 目录: 没有扩展名
    check("拖入目录不生效 (只按扩展名过滤)",
          infos8 == ['没有找到视频文件。'], f"got={infos8!r}")

    win8.bitrate_edit.setText('0')
    infos8.clear()
    win8._on_file_drop(['/tmp/x.mp4'])
    check("阈值非法 → 错误框",
          infos8 == ['码率标准必须为正数。'], f"got={infos8!r}")
    win8.bitrate_edit.setText('30000')
    win8.sample_rate_edit.setText('abc')
    infos8.clear()
    win8._on_file_drop(['/tmp/x.mp4'])
    check("采样率阈值非法 → 错误框",
          infos8 == ['采样率标准必须为正数。'], f"got={infos8!r}")
    win8.sample_rate_edit.setText('48')

    # ---- 含空格的路径不被切错 ----
    # 旧的 tkinterdnd2 私有格式 (DND_FILES) 是空格分隔的一串路径,
    # 含空格的路径会被切错 (产品文档 §6 #35 明说旧实现处理不了)。
    # Qt 走 mimeData().urls() —— 每个 URL 是一个完整路径, 天然正确。
    section("拖拽: 含空格的路径不被切错")
    win8b = MainWindow()
    spaced_dir = mkdtemp(prefix='vdcheck_qt spaced dir ')
    spaced_file = os.path.join(spaced_dir, 'a b c.mp4')
    open(spaced_file, 'w').close()
    md2 = QMimeData()
    md2.setUrls([QUrl.fromLocalFile(spaced_file)])
    # 真拖放是 DragEnter → DragMove* → Drop; 只发 Drop 的话 Qt 不会派发
    # (实测: 单发一个 QDropEvent 收不到, 前面补一个 DragEnter 才行)。
    enter2 = QDragEnterEvent(QPointF(10, 10), Qt.CopyAction, md2,
                              Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(win8b.view.viewport(), enter2)
    drop2 = QDropEvent(QPointF(10, 10), Qt.CopyAction, md2,
                        Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(win8b.view.viewport(), drop2)
    ok = wait_scan(win8b)
    check("含空格的路径扫完了", ok)
    check("含空格的路径**完整保留** (没被切成两半)",
          win8b._selected_files == [spaced_file],
          f"got={win8b._selected_files!r} want={[spaced_file]!r}")
    check("含空格的路径扫出了 1 个结果",
          len(win8b.video_results) == 1,
          f"got={len(win8b.video_results)}")
    check("标题列用的是完整文件名",
          win8b.model.data(win8b.model.index(0, 1), Qt.DisplayRole)
          == './a b c',
          f"got={win8b.model.data(win8b.model.index(0, 1), Qt.DisplayRole)!r}")

    # ================================================================
    section("截图目录: 「开始截图」才建 (按钮 → ensure_snapshot_dir 的口子)")
    winS = MainWindow()
    snap_parent = mkdtemp(prefix='vdcheck_qt_snap_')
    winS._sync_derived_paths(snap_parent)
    snap_dir = winS.snapshot_dest
    check("派生路径阶段截图目录还不存在",
          not os.path.isdir(snap_dir), f"path={snap_dir!r}")
    # 「定点截图」按钮自己**不建**目录 —— 建目录是弹窗里「开始截图」那一步
    winS._show_info = lambda text: None
    winS.open_snapshot_dialog = lambda: None      # Task 6 之前没有弹窗
    winS._on_snapshot_btn_click()
    check("点「定点截图」不建目录 (归弹窗里的「开始截图」建)",
          not os.path.isdir(snap_dir), f"path={snap_dir!r}")
    winS.ensure_snapshot_dir()
    check("ensure_snapshot_dir() 建目录",
          os.path.isdir(snap_dir), f"path={snap_dir!r}")
    # 规矩: 建目录只有两个口子 —— 截图归 ensure_snapshot_dir, 迁移归
    # _move_files。Task 6 的弹窗**必须**走 ensure_snapshot_dir, 自己
    # os.makedirs 就破坏了「扫描/浏览阶段绝不建目录」。
    import inspect
    mw_makedirs = sorted(
        name for name, obj in vars(MainWindow).items()
        if inspect.isfunction(obj) and 'os.makedirs(' in inspect.getsource(obj))
    check("规矩: 只有 ensure_snapshot_dir 与 _move_files 会建目录 "
          "(Task 6 的弹窗必须走 ensure_snapshot_dir)",
          mw_makedirs == ['_move_files', 'ensure_snapshot_dir'],
          f"got={mw_makedirs}")

    # ================================================================
    section("不着色的格用调色板默认色 (不是写死黑色)")
    winC = MainWindow()
    winC._add_result(make_info('bad.mp4', '/x/bad.mp4', passing=False,
                               bitrate=1000.0))
    # 达标行**也要在** —— 序号列的染色 bug 恰恰出在**达标行**:
    # _cell_fg 对达标行**无条件**返回 FG_PASS, 序号列要是漏在 _cell_fg
    # 里就会被染绿 (而不达标行看不出来 —— 那边只有 title/result 等少数
    # 列被染红, 序号列恰好不红)。只测不达标行 = 这条 bug 完全测不出来。
    winC._add_result(make_info('good.mp4', '/x/good.mp4', passing=True,
                               bitrate=50000.0))
    app.processEvents()

    def fg_at(row, col):
        brush = winC.model.data(winC.model.index(row, col), Qt.ForegroundRole)
        return brush.color().name() if brush else None

    check("不达标行的失败列是红色",
          fg_at(0, 1) == '#dc143c', f"got={fg_at(0, 1)}")
    check("不达标行的**非失败列返回 None** (Qt 用调色板默认色)",
          fg_at(0, 2) is None, f"got={fg_at(0, 2)}")
    check("序号列也**不写死颜色** (规格 §3.3「永不染色」, 不是「永远黑色」)",
          fg_at(0, 0) is None, f"got={fg_at(0, 0)}")
    # ↓ 达标行 (row 1)。上一轮只测了不达标行, 达标行的序号列从没被验过 ——
    # 而上一轮出问题的正是**达标行** (序号被染绿)。
    check("**达标行**的序号列**也是 None** (既不绿也不红)",
          fg_at(1, 0) is None, f"got={fg_at(1, 0)}")
    check("对照: 达标行的数据列**确实是绿的** (说明这行真的达标, "
          "上面那条不是「整行都没色」)",
          fg_at(1, 1) == '#228b22', f"got={fg_at(1, 1)}")

    # ================================================================
    section("列宽两个方向的接线")
    win9 = MainWindow()
    win9.show()
    app.processEvents()
    for i in range(len(VIDEO_COLUMNS)):
        check(f"model → view: 第 {i} 列宽度一致",
              win9.view.columnWidth(i) == win9.model._widths[i],
              f"view={win9.view.columnWidth(i)} model={win9.model._widths[i]}")
    win9.view.setColumnWidth(4, 300)
    app.processEvents()
    check("view → model: 用户拖宽第 4 列后模型跟着变",
          win9.model._widths[4] == 300, f"got={win9.model._widths[4]}")
    # 窄列才看得出「按新宽度重新截断」—— 85px 时 '码率(kbps)' 装得下,
    # 断言恒过, 等于没断言。
    win9.view.setColumnWidth(4, 40)
    app.processEvents()
    hdr40 = win9.model.headerData(4, Qt.Horizontal, Qt.DisplayRole)
    check("view → model: 窄列 → 模型宽度跟着变",
          win9.model._widths[4] == 40, f"got={win9.model._widths[4]}")
    check("view → model: 窄列 → 表头按新宽度重新截断 (原文前缀 + 省略号)",
          hdr40.endswith('...') and '码率(kbps)'.startswith(hdr40[:-3]),
          f"got={hdr40!r}")

    # ================================================================
    section("Qt 特有的行为对齐清单 (8 条)")
    header = win9.view.horizontalHeader()

    # #3 表头不二次 elide
    check("#3 表头 setTextElideMode(Qt.ElideNone)",
          header.textElideMode() == Qt.ElideNone,
          f"got={header.textElideMode()}")
    # #4 不画网格线
    check("#4 视图 setShowGrid(False)", win9.view.showGrid() is False)
    # #5 序号列固定 45px, 其余可拖
    check("#5 序号列是 Fixed", header.sectionResizeMode(0) == QHeaderView.Fixed,
          f"got={header.sectionResizeMode(0)}")
    check("#5 序号列宽 45", win9.view.columnWidth(0) == 45,
          f"got={win9.view.columnWidth(0)}")
    check("#5 其余列是 Interactive",
          all(header.sectionResizeMode(i) == QHeaderView.Interactive
              for i in range(1, len(VIDEO_COLUMNS))))
    check("#5 最小列宽 20px", header.minimumSectionSize() == 20,
          f"got={header.minimumSectionSize()}")
    # #6 不用 Qt 的排序指示箭头
    check("#6 setSortingEnabled(False)", win9.view.isSortingEnabled() is False)
    # #7 表头按 section 对齐
    check("#7 模型给 title 列的表头是左对齐",
          win9.model.headerData(1, Qt.Horizontal, Qt.TextAlignmentRole)
          == int(Qt.AlignLeft | Qt.AlignVCenter),
          f"got={win9.model.headerData(1, Qt.Horizontal, Qt.TextAlignmentRole)}")
    check("#7 其余列的表头是居中",
          all(win9.model.headerData(c, Qt.Horizontal, Qt.TextAlignmentRole)
              == int(Qt.AlignCenter) for c in range(2, len(VIDEO_COLUMNS))))
    # 像素级: 表头真的按这个 role 画 (title 靠左, 其余居中)
    img = header.grab().toImage()
    pos1 = header.sectionViewportPosition(1)
    size1 = header.sectionSize(1)
    pos2 = header.sectionViewportPosition(2)
    size2 = header.sectionSize(2)
    ink1 = first_ink(img, pos1, pos1 + size1)
    ink2 = first_ink(img, pos2, pos2 + size2)
    check("#7 像素级: title 列表头靠左 (墨迹贴近左边缘)",
          ink1 is not None and ink1 < size1 // 3,
          f"ink={ink1} size={size1}")
    check("#7 像素级: 其余列表头居中 (墨迹在中线附近)",
          ink2 is not None and abs(ink2 - size2 // 2) < size2 // 3,
          f"ink={ink2} size={size2}")

    # #8 排序后 current / selection 跟着**同一个文件**走
    section("清单 #8: 排序后 current / selection 跟着文件走")
    winA = MainWindow()
    winA.show()
    app.processEvents()
    winA._add_result(make_info('high.mp4', '/x/high.mp4', bitrate=50000.0))
    winA._add_result(make_info('low.mp4', '/x/low.mp4', bitrate=1000.0))
    winA.view.setCurrentIndex(winA.model.index(0, 1))
    app.processEvents()
    check("排序前 current 指向 high.mp4",
          winA.view.currentIndex().data() == './high.mp4',
          f"got={winA.view.currentIndex().data()!r}")
    winA.model.on_header_clicked(4)          # 码率升序
    app.processEvents()
    check("排序后 current 仍指向同一个文件 (high.mp4)",
          winA.view.currentIndex().data() == './high.mp4',
          f"got={winA.view.currentIndex().data()!r}")
    check("排序后行序确实变了 (low 在前)",
          winA.model.data(winA.model.index(0, 1), Qt.DisplayRole)
          == './low.mp4')

    # ================================================================
    section("手改路径即生效 (拖拽留下的文件列表被丢弃)")
    winB = MainWindow()
    winB._selected_files = ['/tmp/dropdir/a.mp4']
    winB.path_edit.setText('/tmp/other')
    app.processEvents()
    check("改路径后 _selected_files 被丢弃", winB._selected_files is None,
          f"got={winB._selected_files!r}")
    check("扫描目标回落到目录",
          winB._scan_target('/tmp/other') == ('dir', '/tmp/other'),
          f"got={winB._scan_target('/tmp/other')!r}")

    # ================================================================
    section("关窗口: 同一个扫描线程只停一遍")
    # closeEvent 与 aboutToQuit 都会调 _stop_scan —— 清了 self._thread,
    # 第二个调用才是 no-op。否则同一个线程被停两遍 (5s + 5s 的 GUI 冻结),
    # 而且会孤儿化出两个条目, main() 对同一个对象等两次。
    winX = MainWindow()
    winX.path_edit.setText(src)
    winX._start_scan()
    try:
        winX._stop_scan(timeout_ms=0)      # 立刻放弃等待 → 孤儿化
        check("_stop_scan 之后 self._thread 被清掉",
              winX._thread is None, f"got={winX._thread!r}")
        n_before = len(video_qt._ORPHANED_THREADS)
        check("孤儿线程被记进 _ORPHANED_THREADS (退出时统一收)",
              n_before >= 1, f"got={n_before}")
        winX._stop_scan(timeout_ms=0)      # 再来一次 → 必须是 no-op
        check("再停一次不会重复孤儿化 (同一个线程只停一遍)",
              len(video_qt._ORPHANED_THREADS) == n_before,
              f"before={n_before} after={len(video_qt._ORPHANED_THREADS)}")
    finally:
        # 收尾: 等那些孤儿线程结束。**引用不能无条件清** ——
        # clear() = 丢掉 QThread 的 Python 引用 = 析构那个线程; 它要是
        # 还在跑 → QThread: Destroyed while thread is still running →
        # qFatal → **整个脚本以 134 死掉, 连失败清单都出不来**。
        # wait() 的返回值必须看: 5s 内没停的线程**绝不能**从列表里摘掉。
        still_running = []
        for thread in list(video_qt._ORPHANED_THREADS):
            if not thread.wait(5000):
                still_running.append(thread)
        if still_running:
            # 还在跑 → 引用留着 (宁可解释器收尾时炸, 也不能在**断言
            # 脚本运行中**主动析构一个活着的线程)。
            print(f"  ! {len(still_running)} 个孤儿线程 5s 内没停 "
                  f"(引用保留, 不清)")
        else:
            video_qt._ORPHANED_THREADS.clear()
        app.processEvents()

    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
