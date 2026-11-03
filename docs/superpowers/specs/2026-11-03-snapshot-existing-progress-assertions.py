"""「查看已有截图」的**加载进度**行为断言。

对照 docs/产品文档.md §3.5 (定点截图) 与 §4.3 (文案全集):
点「查看已有截图」后, 底栏状态行要**实时**显示分析/加载进度 ——
「分析中 N/M 个文件」→「加载中 K/T (当前视频名)」→「已有截图: …」。
加载跑在**后台线程**里 (界面全程可动), 队列**限长** (背压, 内存不涨)。

跑法:
    QT_QPA_PLATFORM=offscreen python docs/superpowers/specs/2026-11-03-snapshot-existing-progress-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import queue
import shutil
import sys
import tempfile
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
sys.path.insert(0, _ROOT)

_tmpdirs: list[str] = []


def mkdtemp(prefix: str = 'vdcheck_') -> str:
    path = tempfile.mkdtemp(prefix=prefix)
    _tmpdirs.append(path)
    return path


def cleanup_tmpdirs() -> None:
    for path in _tmpdirs:
        shutil.rmtree(path, ignore_errors=True)
    _tmpdirs.clear()


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import video_qt  # noqa: E402
from video_core import VideoInfo  # noqa: E402

_failures: list[str] = []
real_extract = video_qt.extract_frame


def stub_msgbox(kind: str):
    def _stub(parent, title, text, *args, **kwargs):
        return QMessageBox.Ok
    return _stub


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "✓" if condition else "✗"
    line = f"  {mark} {name}" + (f" — {detail}" if detail else "")
    print(line)
    if not condition:
        _failures.append(line)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def make_info(title='a.mp4', full_path='/x/a.mp4') -> VideoInfo:
    return VideoInfo(
        rel_path='./', title=title, resolution='1920x1080',
        frame_rate='30.00', bitrate_kbps=32000.0, video_codec='h264',
        audio_codec='aac', audio_channels='2', audio_sample_rate='48.0',
        duration='00:01:00', file_size='1.0 GB', is_passing=True,
        sample_rate_passing=True, bitrate_std=30000.0, sample_rate_std=48.0,
        full_path=full_path, container_format='mp4',
        frame_rate_fps=30.0, sample_rate_khz=48.0)


def fake_png_bytes(width: int = 640, height: int = 360) -> bytes:
    img = QImage(width, height, QImage.Format_RGB32)
    img.fill(QColor('red'))
    path = os.path.join(mkdtemp(), 'x.png')
    img.save(path, 'PNG')
    with open(path, 'rb') as f:
        data = f.read()
    os.remove(path)
    return data


def write_png(directory: str, name: str) -> str:
    path = os.path.join(directory, name)
    with open(path, 'wb') as f:
        f.write(fake_png_bytes())
    return path


def pump_until(predicate, timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    app.processEvents()
    return bool(predicate())


def find_button(dlg, text: str):
    for btn in dlg.findChildren(QPushButton):
        if btn.text() == text:
            return btn
    return None


def spy_label(dlg) -> list[str]:
    """给状态行的 setText 打补丁, 记录**每一次**调用。

    QLabel 实例可以打 Python 属性 (PySide6 允许) —— 这样即使一次
    poll 周期里连着设了好几次, 也能看到完整的文案序列 (不用赌时序)。
    """
    seen: list[str] = []
    label = dlg.status_label
    orig = label.setText

    def spy(text):
        seen.append(text)
        orig(text)
    label.setText = spy
    return seen


# ======================================================================
# A. 后台线程的消息流 (协议层)
# ======================================================================

def test_worker_protocol() -> None:
    section("后台线程: 消息流有完整的两段进度")
    out = mkdtemp()
    write_png(out, 'a_t1_00-00-07-24.png')
    write_png(out, 'a_t2_00-00-08-00.png')
    write_png(out, 'b_t1_00-00-07-24.png')
    write_png(out, 'noise.png')          # 不合规 → 只算进「分析」的文件数
    files = [make_info('a.mp4'), make_info('b.mp4')]

    q: queue.Queue = queue.Queue()
    video_qt._existing_worker(files, out, threading.Event(), q)
    msgs = []
    while not q.empty():
        msgs.append(q.get_nowait())
    kinds = [m[0] for m in msgs]
    print(f"    消息: {kinds}")

    check("有 'existing_progress' 消息", 'existing_progress' in kinds)
    prog = [m[1] for m in msgs if m[0] == 'existing_progress']
    phases = {p[0] for p in prog}
    check("进度分 'analyze' 与 'load' 两段",
          phases == {'analyze', 'load'}, f"got={phases}")
    analyze = [p for p in prog if p[0] == 'analyze']
    check("分析段: 总量 = 目录里的文件数 (含不合规的)",
          analyze[-1][2] == 4, f"got={analyze[-1]}")
    check("分析段: 计数单调递增到总量",
          [p[1] for p in analyze] == sorted(p[1] for p in analyze)
          and analyze[-1][1] == 4,
          f"got={[p[1] for p in analyze]}")
    load = [p for p in prog if p[0] == 'load']
    check("加载段: 总量 = 命中的张数 (3)",
          load[-1][2] == 3, f"got={load[-1]}")
    check("加载段: 计数从 0 走到总量",
          load[0][1] == 0 and load[-1][1] == 3, f"got={load}")

    check("有 'existing_columns' 消息", 'existing_columns' in kinds)
    cols = [m[1] for m in msgs if m[0] == 'existing_columns'][0][0]
    check("列 = 目录里的时间点, 按时间排序",
          cols == ['00:00:07:24', '00:00:08:00'], f"got={cols}")

    cells = [m[1] for m in msgs if m[0] == 'existing_cell']
    check("命中 3 格", len(cells) == 3, f"got={len(cells)}")
    check("格子里带的是 QImage (解码好的), 不是路径",
          all(isinstance(c[2], QImage) for c in cells),
          f"got={[type(c[2]).__name__ for c in cells]}")
    check("QImage 已经缩到 320 宽",
          all(c[2].width() == 320 for c in cells),
          f"got={[c[2].width() for c in cells]}")
    check("同时带磁盘路径 (放大窗口要用)",
          all(isinstance(c[3], str) and os.path.isfile(c[3]) for c in cells))

    empties = [m[1] for m in msgs if m[0] == 'existing_empty']
    check("落空的格子单独发 'existing_empty' (b 的第 2 个时间点)",
          len(empties) == 1 and empties[0] == (1, 1),
          f"got={empties}")

    done = [m[1] for m in msgs if m[0] == 'existing_done']
    check("最后是 'existing_done' (3 张 / 共 3 张 / 2 列)",
          len(done) == 1 and done[0] == (3, 3, 2), f"got={done}")
    check("'existing_done' 在最后",
          kinds[-1] == 'existing_done', f"got={kinds[-1]}")


def test_worker_no_images() -> None:
    section("后台线程: 目录里没有合规的图")
    out = mkdtemp()
    write_png(out, '随便.png')
    q: queue.Queue = queue.Queue()
    video_qt._existing_worker([make_info('a.mp4')], out, threading.Event(), q)
    msgs = []
    while not q.empty():
        msgs.append(q.get_nowait())
    kinds = [m[0] for m in msgs]
    check("不发 'existing_columns' (网格不重建)",
          'existing_columns' not in kinds, f"got={kinds}")
    done = [m[1] for m in msgs if m[0] == 'existing_done']
    check("existing_done = (0, 0, 0)",
          done == [(0, 0, 0)], f"got={done}")


def test_worker_cancel() -> None:
    section("后台线程: 取消标志能停下来")
    out = mkdtemp()
    for i in range(20):
        write_png(out, f'a_t{i + 1}_00-00-0{i}-00.png')
    cancel = threading.Event()
    cancel.set()
    q: queue.Queue = queue.Queue()
    video_qt._existing_worker([make_info('a.mp4')], out, cancel, q)
    msgs = []
    while not q.empty():
        msgs.append(q.get_nowait())
    kinds = [m[0] for m in msgs]
    check("取消后不发任何格子", 'existing_cell' not in kinds, f"got={kinds}")
    check("取消后不发 existing_done (主线程靠按钮恢复状态)",
          'existing_done' not in kinds, f"got={kinds}")


# ======================================================================
# B. 界面层
# ======================================================================

def test_dialog_progress() -> None:
    win = video_qt.MainWindow()
    out = mkdtemp()
    write_png(out, 'a_t1_00-00-07-24.png')
    write_png(out, 'a_t2_00-00-08-00.png')
    write_png(out, 'b_t1_00-00-07-24.png')
    files = [make_info('a.mp4'), make_info('b.mp4')]
    dlg = video_qt.SnapshotDialog(win, files, out)

    section("界面: 状态行依次显示 分析 → 加载 → 完成")
    seen = spy_label(dlg)
    dlg._on_view_existing()
    check("点完按钮立刻进入加载态", dlg._loading_existing is True)
    finished = pump_until(lambda: not dlg._loading_existing, timeout=20)
    check("加载跑完了", finished, f"loading={dlg._loading_existing}")
    print(f"    状态行序列: {seen}")

    analyze = [t for t in seen if t.startswith('分析中')]
    load = [t for t in seen if t.startswith('加载中')]
    check("有「分析中 N/M 个文件」", bool(analyze), f"got={analyze}")
    check("有「加载中 K/T (视频名)」",
          bool(load) and any('(' in t for t in load), f"got={load}")
    check("分析在前、加载在后",
          seen.index(analyze[-1]) < seen.index(load[0]) if analyze and load else False,
          f"got={seen}")
    check("最后一条是完成文案",
          seen and seen[-1].startswith('已有截图:'), f"got={seen[-1] if seen else None}")
    check("完成文案里有张数与时间点数",
          seen[-1] == '已有截图: 3 张已载入 (共 3 张, 2 个时间点取自目录)',
          f"got={seen[-1] if seen else None}")

    section("界面: 加载期间按钮禁用, 完成后恢复")
    check("「开始截图」可用", find_button(dlg, '开始截图').isEnabled())
    check("「查看已有截图」可用",
          find_button(dlg, '查看已有截图').isEnabled())

    section("界面: 加载完网格是对的")
    check("列数 = 2 + 时间点数", dlg.model.columnCount() == 4,
          f"got={dlg.model.columnCount()}")
    check("a 的第 1 格 = done",
          dlg.model.cell_state(0, 2).status == 'done',
          f"got={dlg.model.cell_state(0, 2)}")
    check("b 的第 2 格 = empty",
          dlg.model.cell_state(1, 3).status == 'empty',
          f"got={dlg.model.cell_state(1, 3)}")
    check("进度条 = 100%",
          dlg.progress.value() == dlg.progress.maximum(),
          f"value={dlg.progress.value()} max={dlg.progress.maximum()}")


def test_dialog_busy_buttons() -> None:
    win = video_qt.MainWindow()
    out = mkdtemp()
    for i in range(30):
        write_png(out, f'a_t{i + 1}_00-00-{i:02d}-00.png')
    dlg = video_qt.SnapshotDialog(win, [make_info('a.mp4')], out)

    section("界面: 加载期间两个按钮都禁用")
    dlg._on_view_existing()
    btn_view = find_button(dlg, '查看已有截图')
    btn_snap = find_button(dlg, '开始截图')
    check("加载期间「查看已有截图」禁用", not btn_view.isEnabled())
    check("加载期间「开始截图」禁用", not btn_snap.isEnabled())
    check("加载期间点「开始截图」被忽略",
          (dlg._on_action_btn(), dlg._state != 'running')[1],
          f"state={dlg._state}")
    pump_until(lambda: not dlg._loading_existing, timeout=30)
    check("跑完「开始截图」恢复可用", btn_snap.isEnabled())
    check("跑完「查看已有截图」恢复可用", btn_view.isEnabled())


def test_dialog_close_during_load() -> None:
    win = video_qt.MainWindow()
    out = mkdtemp()
    for i in range(40):
        write_png(out, f'a_t{i + 1}_00-00-{i:02d}-00.png')
    dlg = video_qt.SnapshotDialog(win, [make_info('a.mp4')], out)

    section("界面: 加载中关窗口不崩")
    dlg._on_view_existing()
    dlg.close()          # 走 closeEvent → _finish_close
    pump_until(lambda: False, timeout=0.3)   # 让事件循环转一转
    check("关窗没抛异常", True)
    check("关窗后取消标志置上了", dlg._existing_cancel.is_set())
    check("关窗后 _closed 为真", dlg._closed is True)
    # 后台线程是 daemon, 自己会退出; 等它一下, 别留个野线程
    th = getattr(dlg, '_existing_thread', None)
    if th is not None:
        th.join(timeout=10)
        check("后台线程已退出", not th.is_alive())


def main() -> int:
    orig_warning = QMessageBox.warning
    orig_critical = QMessageBox.critical
    orig_question = QMessageBox.question
    QMessageBox.warning = stub_msgbox('warning')
    QMessageBox.critical = stub_msgbox('critical')
    QMessageBox.question = stub_msgbox('question')
    try:
        test_worker_protocol()
        test_worker_no_images()
        test_worker_cancel()
        test_dialog_progress()
        test_dialog_busy_buttons()
        test_dialog_close_during_load()
    finally:
        QMessageBox.warning = orig_warning
        QMessageBox.critical = orig_critical
        QMessageBox.question = orig_question
        video_qt.extract_frame = real_extract
        cleanup_tmpdirs()

    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
