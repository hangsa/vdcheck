"""定点截图的「删除时间点」与「逆时间点」断言。

对照 2026-11-04 的两条新需求:

    1. 添加的时间点可以删除 (包括默认时间点);
    2. 增加时间点类型 —— 默认正时间点, 逆时间点 = 从片尾往片头算
       (实际截图时刻 = 视频时长 - 输入的时间点)。

跑法:
    QT_QPA_PLATFORM=offscreen python docs/superpowers/specs/2026-11-04-snapshot-reverse-assertions.py
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


def mkdtemp(prefix: str = 'vdcheck_rev_') -> str:
    path = tempfile.mkdtemp(prefix=prefix)
    _tmpdirs.append(path)
    return path


def cleanup_tmpdirs() -> None:
    for path in _tmpdirs:
        shutil.rmtree(path, ignore_errors=True)
    _tmpdirs.clear()


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import video_core  # noqa: E402
import video_qt  # noqa: E402
from video_core import VideoInfo  # noqa: E402

_failures: list[str] = []
msgbox_log: list[tuple[str, str, str]] = []


def stub_msgbox(kind: str):
    def _stub(parent, title, text, *args, **kwargs):
        msgbox_log.append((kind, title, text))
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


def make_info(title='a.mp4', full_path='/x/a.mp4', duration_seconds=0.0) -> VideoInfo:
    return VideoInfo(
        rel_path='./', title=title, resolution='1920x1080',
        frame_rate='30.00', bitrate_kbps=32000.0, video_codec='h264',
        audio_codec='aac', audio_channels='2', audio_sample_rate='48.0',
        duration='00:02:00', file_size='1.0 GB', is_passing=True,
        sample_rate_passing=True, bitrate_std=30000.0, sample_rate_std=48.0,
        full_path=full_path, container_format='mp4',
        frame_rate_fps=30.0, sample_rate_khz=48.0,
        duration_seconds=duration_seconds)


# ---------------------------------------------------------------- 纯逻辑

def test_marker():
    section('逆时间点的 R 标记')
    check('R 前缀能被认出来', video_core.is_reverse_token('R00:01:00:00'))
    check('正时间点不带 R', not video_core.is_reverse_token('00:01:00:00'))
    check('剥掉 R', video_core.strip_reverse_marker('R00:01:00:00')
          == '00:01:00:00')
    check('正的不受影响',
          video_core.strip_reverse_marker('00:01:00:00') == '00:01:00:00')
    check('parse_time_token 会自动剥掉 R',
          video_core.parse_time_token('R00:01:00:00', 30.0) == 60.0,
          f"got={video_core.parse_time_token('R00:01:00:00', 30.0)}")
    check('文件名里的 R 保留 (Windows 不能有冒号)',
          video_core.time_token_to_string('R00:01:00:00') == 'R00-01-00-00',
          f"got={video_core.time_token_to_string('R00:01:00:00')!r}")
    check('排序不受 R 影响 (按数值, 不按字符串)',
          video_core.time_token_sort_key('R00:01:00:00') == (0, 1, 0, 0),
          f"got={video_core.time_token_sort_key('R00:01:00:00')}")
    check('R 的排在同秒的正时间点后面 (同一数值时)',
          video_core.time_token_sort_key('R00:00:07:24')
          == video_core.time_token_sort_key('00:00:07:24'))


def test_actual_seconds():
    section('实际截图时刻')
    check('正时间点 = 输入值',
          video_core.actual_seconds_for_token('00:00:10:00', 30.0, 120.0)
          == 10.0)
    check('逆时间点 = 时长 - 输入值',
          video_core.actual_seconds_for_token('R00:00:10:00', 30.0, 120.0)
          == 110.0,
          f"got={video_core.actual_seconds_for_token('R00:00:10:00', 30.0, 120.0)}")
    check('逆时间点: 帧也算进偏移',
          abs(video_core.actual_seconds_for_token('R00:00:10:15', 30.0, 120.0)
              - 109.5) < 1e-6)
    try:
        video_core.actual_seconds_for_token('R00:10:00:00', 30.0, 300.0)
        check('逆时间点超过片长 → 报错', False, '没抛异常')
    except ValueError as exc:
        check('逆时间点超过片长 → 报错', '片长' in str(exc), f"got={str(exc)!r}")


def test_filename_roundtrip():
    section('截图文件名 (带 R 的)')
    check('逆的能解析出来',
          video_core.parse_snapshot_filename('a_t1_R00-01-00-00.png')
          == ('a', 'R00:01:00:00'),
          f"got={video_core.parse_snapshot_filename('a_t1_R00-01-00-00.png')!r}")
    check('旧的 (没 R 的) 照旧能读',
          video_core.parse_snapshot_filename('a_t1_00-00-07-24.png')
          == ('a', '00:00:07:24'),
          f"got={video_core.parse_snapshot_filename('a_t1_00-00-07-24.png')!r}")
    check('不合规的名字仍然返回 None',
          video_core.parse_snapshot_filename('随便.png') is None)


# ---------------------------------------------------------------- 弹窗

def test_dialog_defaults():
    section('默认时间点: 前两个正、第 3 个逆')
    dlg = video_qt.SnapshotDialog(None, [make_info()], '/tmp/x')
    try:
        kinds = [g.kind for g in dlg._tp_groups]
        check('默认三个时间点的类型', kinds == ['正', '正', '逆'], f"got={kinds}")
        check('默认三个时间点的值',
              [e.text() for e in dlg._tp_entries]
              == ['00:00:07:24', '00:00:08:00', '00:01:00:00'])
        check('每个时间点都带一个删除按钮',
              sum(1 for b in dlg.findChildren(QPushButton) if b.text() == '×') == 3,
              f"got={[b.text() for b in dlg.findChildren(QPushButton)]}")
        # 正时间点**不加**标记; 逆的标记是 'R' (2026-11-04 用户要求)
        check('正时间点前面没有标记 (不加「正」字)',
              dlg._tp_groups[0].label is None
              and dlg._tp_groups[1].label is None,
              f"got={[g.label.text() if g.label else None for g in dlg._tp_groups]}")
        check('逆时间点前面的标记是「R」',
              dlg._tp_groups[2].label is not None
              and dlg._tp_groups[2].label.text() == 'R',
              f"got={dlg._tp_groups[2].label.text() if dlg._tp_groups[2].label else None!r}")
    finally:
        dlg.close()


def test_delete():
    section('删除时间点 (默认的也能删)')
    dlg = video_qt.SnapshotDialog(None, [make_info()], '/tmp/x')
    try:
        before = len(dlg._tp_groups)
        dlg._tp_groups[0].delete_btn.click()
        app.processEvents()
        check('删掉第一个 (默认的) → 少一个',
              len(dlg._tp_groups) == before - 1,
              f"got={len(dlg._tp_groups)}")
        check('删掉的正是第一个',
              [g.edit.text() for g in dlg._tp_groups]
              == ['00:00:08:00', '00:01:00:00'],
              f"got={[g.edit.text() for g in dlg._tp_groups]}")
        # 全删光
        for group in list(dlg._tp_groups):
            group.delete_btn.click()
        app.processEvents()
        check('全删光 → 没有时间点了', len(dlg._tp_groups) == 0,
              f"got={len(dlg._tp_groups)}")
        check('全删光 → 没有输入框了', dlg._tp_entries == [])
        # 点「开始截图」→ 提示至少一个时间点
        msgbox_log.clear()
        dlg._on_action_btn()
        check('全删光点「开始截图」→ 提示至少一个时间点',
              any('至少一个时间点' in text for _k, _t, text in msgbox_log),
              f"got={msgbox_log}")
    finally:
        dlg.close()


def test_add_menu():
    section('「+」菜单: 添加时选正/逆')
    dlg = video_qt.SnapshotDialog(None, [make_info()], '/tmp/x')
    try:
        menu = dlg.add_btn.menu()
        check('「+」带菜单', menu is not None)
        texts = [a.text() for a in menu.actions()]
        check('菜单里有「正时间点」「逆时间点」',
              texts == ['正时间点', '逆时间点'], f"got={texts}")
        before = len(dlg._tp_groups)
        menu.actions()[1].trigger()      # 逆时间点
        app.processEvents()
        check('选「逆时间点」→ 加进来一个逆的',
              len(dlg._tp_groups) == before + 1
              and dlg._tp_groups[-1].kind == '逆',
              f"got={[(g.kind) for g in dlg._tp_groups]}")
        check('新加的值是 00:00:00:00',
              dlg._tp_groups[-1].edit.text() == '00:00:00:00')
        menu.actions()[0].trigger()      # 正时间点
        app.processEvents()
        check('选「正时间点」→ 加进来一个正的',
              dlg._tp_groups[-1].kind == '正')
    finally:
        dlg.close()


def test_header_marker():
    section('网格表头标出逆时间点')
    dlg = video_qt.SnapshotDialog(None, [make_info()], '/tmp/x')
    try:
        model = dlg.model
        model.set_time_tokens(['00:00:07:24', 'R00:01:00:00'])
        head2 = model.headerData(2, Qt.Horizontal)
        head3 = model.headerData(3, Qt.Horizontal)
        check('正时间点的表头', '00-00-07-24' in head2, f"got={head2!r}")
        check('逆时间点的表头带「R」', 'R' in head3, f"got={head3!r}")
        check('逆时间点的表头不含「逆」', '逆' not in head3, f"got={head3!r}")
        check('逆时间点的表头 = R + 时间点 (与文件名同形)',
              head3.endswith('R00-01-00-00'), f"got={head3!r}")
    finally:
        dlg.close()


def test_worker():
    section('worker: 逆时间点的实际秒数与文件名')
    out = mkdtemp()
    calls = []
    real = video_qt.extract_frame

    def fake_extract(path, seconds, out_path):
        calls.append((seconds, out_path))
        return True

    video_qt.extract_frame = fake_extract
    try:
        # 正
        q = queue.Queue()
        video_qt._snapshot_worker([make_info(duration_seconds=120.0)],
                                  ['00:00:10:00'], out,
                                  threading.Event(), running_pause(), set(), q)
        check('正时间点: 秒数 = 输入值', calls[0][0] == 10.0,
              f"got={calls[0][0]}")
        check('正时间点: 文件名没有 R',
              os.path.basename(calls[0][1]) == 'a_t1_00-00-10-00.png',
              f"got={os.path.basename(calls[0][1])!r}")

        # 逆
        calls.clear()
        q = queue.Queue()
        video_qt._snapshot_worker([make_info(duration_seconds=120.0)],
                                  ['R00:00:10:00'], out,
                                  threading.Event(), running_pause(), set(), q)
        check('逆时间点: 秒数 = 时长 - 输入值', calls[0][0] == 110.0,
              f"got={calls[0][0]}")
        check('逆时间点: 文件名带 R',
              os.path.basename(calls[0][1]) == 'a_t1_R00-00-10-00.png',
              f"got={os.path.basename(calls[0][1])!r}")

        # 超过片长 → 那一格失败, 不抽帧
        calls.clear()
        q = queue.Queue()
        video_qt._snapshot_worker([make_info(duration_seconds=300.0)],
                                  ['R00:10:00:00'], out,
                                  threading.Event(), running_pause(), set(), q)
        check('逆时间点超过片长 → 不抽帧', calls == [], f"got={calls}")
        msgs = []
        while not q.empty():
            msgs.append(q.get())
        check('逆时间点超过片长 → 那一格标失败',
              any(m[0] == 'cell_failed' for m in msgs),
              f"got={[m[0] for m in msgs]}")
    finally:
        video_qt.extract_frame = real


def test_start_snap_tokens():
    section('起跑时: 逆的时间点带 R 前缀')
    win = video_qt.MainWindow()
    dlg = video_qt.SnapshotDialog(win, [make_info()], '/tmp/x')
    try:
        dlg._tp_groups[2].edit.setText('00:00:30:00')
        dlg.output_edit.setText(mkdtemp())
        dlg._on_action_btn()
        deadline = time.time() + 10
        while dlg._state == 'running' and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        check('起跑后第 3 列是逆时间点',
              dlg.model.time_tokens[2] == 'R00:00:30:00',
              f"got={dlg.model.time_tokens!r}")
        check('前两列是正的 (不带 R)',
              dlg.model.time_tokens[:2] == ['00:00:07:24', '00:00:08:00'],
              f"got={dlg.model.time_tokens!r}")
    finally:
        dlg.close()
        win.close()


def running_pause() -> threading.Event:
    """一个「在跑」状态的暂停标志 (set = 不暂停)。"""
    event = threading.Event()
    event.set()
    return event


def fake_png_bytes(width: int = 64, height: int = 36) -> bytes:
    """一张纯色 PNG —— 缩略图要从磁盘读, 得是真图片。"""
    from PySide6.QtGui import QColor, QImage
    img = QImage(width, height, QImage.Format_RGB32)
    img.fill(QColor('red'))
    path = os.path.join(mkdtemp(), 'x.png')
    img.save(path, 'PNG')
    with open(path, 'rb') as f:
        data = f.read()
    os.remove(path)
    return data


def test_worker_skip():
    """worker: skip_cells 里的格子不重抽 (2026-11-04 用户要求)。"""
    section('worker: 跳过已抽好的格子')
    out = mkdtemp()
    calls = []
    real = video_qt.extract_frame

    def fake_extract(path, seconds, out_path):
        calls.append((os.path.basename(out_path), seconds))
        with open(out_path, 'wb') as f:
            f.write(fake_png_bytes())
        return True

    video_qt.extract_frame = fake_extract
    try:
        q = queue.Queue()
        video_qt._snapshot_worker(
            [make_info(duration_seconds=120.0)],
            ['00:00:10:00', '00:00:20:00'], out,
            threading.Event(), running_pause(),
            {(0, '00:00:10:00')},          # ← 这格已经截好了
            q)
        check('已抽好的那格不重抽 (只抽了 1 格)',
              len(calls) == 1, f"got={calls}")
        check('抽的是**没抽过**的那格', calls and calls[0][1] == 20.0,
              f"got={calls}")
        msgs = []
        while not q.empty():
            msgs.append(q.get())
        done = [m for m in msgs if m[0] == 'done']
        check('跳过的格子算作已完成 (2/2)',
              done and done[0][1][0] == 2,
              f"got={done[0][1] if done else None}")
    finally:
        video_qt.extract_frame = real


def test_worker_pause():
    """worker: 暂停标志没 set → 一格都不抽。"""
    section('worker: 暂停标志')
    out = mkdtemp()
    calls = []
    real = video_qt.extract_frame

    def fake_extract(path, seconds, out_path):
        calls.append(seconds)
        with open(out_path, 'wb') as f:
            f.write(fake_png_bytes())
        return True

    video_qt.extract_frame = fake_extract
    try:
        pause = threading.Event()
        pause.clear()                       # 暂停中
        q = queue.Queue()
        thread = threading.Thread(
            target=video_qt._snapshot_worker,
            args=([make_info(duration_seconds=120.0)],
                  ['00:00:10:00', '00:00:20:00'], out,
                  threading.Event(), pause, set(), q),
            daemon=True)
        thread.start()
        time.sleep(0.5)
        check('暂停中 → 一格都没抽', calls == [], f"got={calls}")
        pause.set()                         # 继续
        thread.join(10)
        check('继续后抽完', len(calls) == 2, f"got={calls}")
    finally:
        video_qt.extract_frame = real


def test_dialog_pause_resume():
    """弹窗: 暂停 → 继续, 已截好的不重抽, 新增的时间点照常抽。"""
    section('弹窗: 暂停 / 继续')
    win = video_qt.MainWindow()
    out = mkdtemp()
    calls = []
    real = video_qt.extract_frame

    def fake_extract(path, seconds, out_path):
        calls.append(os.path.basename(out_path))
        time.sleep(0.03)
        with open(out_path, 'wb') as f:
            f.write(fake_png_bytes())
        return True

    video_qt.extract_frame = fake_extract
    try:
        # 时长要给够 —— 默认第 3 个时间点是**逆**的, 没时长会失败
        dlg = video_qt.SnapshotDialog(win, [make_info('a.mp4',
                                                      duration_seconds=3600.0),
                                            make_info('b.mp4',
                                                      duration_seconds=3600.0)],
                                      out)
        dlg._on_action_btn()
        check('起跑后按钮是「截图中」', dlg.action_btn.text() == '截图中',
              f"got={dlg.action_btn.text()!r}")
        # 等第一格抽完 (模型里标上 done)
        deadline = time.time() + 10
        while dlg._cells_processed < 1 and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        check('至少抽完一格', dlg._cells_processed >= 1,
              f"got={dlg._cells_processed}")
        # 点一下 → 暂停
        dlg.action_btn.click()
        check('点一下 → 状态是 paused', dlg._state == 'paused',
              f"got={dlg._state!r}")
        check('按钮变「继续截图」', dlg.action_btn.text() == '继续截图',
              f"got={dlg.action_btn.text()!r}")
        check('状态行报「已暂停」', '暂停' in dlg.status_label.text(),
              f"got={dlg.status_label.text()!r}")
        check('暂停期间时间点可以改', dlg._tp_entries[0].isEnabled())
        # 等 worker 真的停下 (抽帧调用不再增加) —— 这样模型才是最新的
        last = len(calls)
        deadline = time.time() + 10
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.05)
            if len(calls) == last:
                break
            last = len(calls)
        app.processEvents()
        # 暂停期间新增一个时间点
        dlg._add_time_token_entry('00:00:30:00', '正')
        # 继续
        dlg.action_btn.click()
        check('点「继续截图」→ 又在跑', dlg._state == 'running',
              f"got={dlg._state!r}")
        deadline = time.time() + 20
        while dlg._state == 'running' and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        check('跑完了', dlg._state != 'running', f"got={dlg._state!r}")
        # 2 个文件 × 4 个时间点 = 8 格 —— **每格只抽一次**
        check('每格只抽了一次 (已截好的没重抽)',
              len(calls) == 8, f"got={len(calls)} 次 {calls}")
        check('新增时间点的图也抽了',
              any('00-00-30-00' in name for name in calls),
              f"got={calls}")
        dlg.close()
    finally:
        video_qt.extract_frame = real
        win.close()


def main() -> int:
    orig_warning = QMessageBox.warning
    orig_critical = QMessageBox.critical
    QMessageBox.warning = stub_msgbox('warning')
    QMessageBox.critical = stub_msgbox('critical')
    try:
        test_marker()
        test_actual_seconds()
        test_filename_roundtrip()
        test_dialog_defaults()
        test_delete()
        test_add_menu()
        test_header_marker()
        test_worker()
        test_start_snap_tokens()
        test_worker_skip()
        test_worker_pause()
        test_dialog_pause_resume()
    finally:
        QMessageBox.warning = orig_warning
        QMessageBox.critical = orig_critical
        cleanup_tmpdirs()

    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
