"""定点截图的「清除截图」断言 (2026-11-13)。

需求:
    定点截图弹窗加一个「清除截图」按钮 —— 截图完成 / 暂停截图时可用;
    点它把**界面上展示的**截图抹掉, 并删掉**对应的截图文件**。

跑法:
    QT_QPA_PLATFORM=offscreen python docs/superpowers/specs/2026-11-13-snapshot-clear-assertions.py
退出码 0 = 全部通过; 非 0 = 有失败。
"""
import os
import shutil
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
sys.path.insert(0, _ROOT)

_tmpdirs: list[str] = []


def mkdtemp(prefix: str = 'vdcheck_clear_') -> str:
    path = tempfile.mkdtemp(prefix=prefix)
    _tmpdirs.append(path)
    return path


def cleanup_tmpdirs() -> None:
    for path in _tmpdirs:
        shutil.rmtree(path, ignore_errors=True)
    _tmpdirs.clear()


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import video_core  # noqa: E402
import video_qt  # noqa: E402
from video_core import VideoInfo  # noqa: E402

_failures: list[str] = []
msgbox_log: list[tuple[str, str, str]] = []
# 「确认清除」弹框的答复 —— 测试里改它 (Yes / No)
confirm_answer = {'value': QMessageBox.Yes}


def stub_msgbox(kind: str):
    def _stub(parent, title, text, *args, **kwargs):
        msgbox_log.append((kind, title, text))
        return QMessageBox.Ok
    return _stub


def stub_question(parent, title, text, *args, **kwargs):
    """「确认清除」的答复由 confirm_answer 控制。"""
    msgbox_log.append(('question', title, text))
    return confirm_answer['value']


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "✓" if condition else "✗"
    line = f"  {mark} {name}" + (f" — {detail}" if detail else "")
    print(line)
    if not condition:
        _failures.append(line)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def make_info(title='a.mp4', full_path='/x/a.mp4',
              duration_seconds=3600.0) -> VideoInfo:
    return VideoInfo(
        rel_path='./', title=title, resolution='1920x1080',
        frame_rate='30.00', bitrate_kbps=32000.0, video_codec='h264',
        audio_codec='aac', audio_channels='2', audio_sample_rate='48.0',
        duration='01:00:00', file_size='1.0 GB', is_passing=True,
        sample_rate_passing=True, bitrate_std=30000.0, sample_rate_std=48.0,
        full_path=full_path, container_format='mp4',
        frame_rate_fps=30.0, sample_rate_khz=48.0,
        duration_seconds=duration_seconds)


def make_png(path: str, width: int = 64, height: int = 36) -> str:
    """往 path 写一张**真** PNG —— 缩略图要从磁盘读, 得是真图片。"""
    img = QImage(width, height, QImage.Format_RGB32)
    img.fill(QColor('red'))
    img.save(path, 'PNG')
    return path


def fill_done(dlg, cells: list[tuple[int, int, str]]) -> None:
    """把若干格标成「已抽好」并贴上缩略图。

    cells = [(行号, 列号, 文件路径)] —— 列号是**时间点列**(从 2 起)。
    """
    for row, col, path in cells:
        dlg.delegate.set_thumbnail(row, col, path)
        dlg.model.mark_done(row, col, path)


# ---------------------------------------------------------------- 纯逻辑

def test_unique_paths():
    section('unique_snapshot_paths (去重)')
    check('同一个路径只留一个',
          video_core.unique_snapshot_paths(['/a/x.png', '/a/x.png'])
          == ['/a/x.png'],
          f"got={video_core.unique_snapshot_paths(['/a/x.png', '/a/x.png'])}")
    check('保持原有顺序',
          video_core.unique_snapshot_paths(
              ['/a/1.png', '/a/2.png', '/a/1.png'])
          == ['/a/1.png', '/a/2.png'],
          f"got={video_core.unique_snapshot_paths(['/a/1.png', '/a/2.png', '/a/1.png'])}")
    check('相对路径与绝对路径算同一个',
          video_core.unique_snapshot_paths(['/a/x.png', '/a/./x.png'])
          == ['/a/x.png'],
          f"got={video_core.unique_snapshot_paths(['/a/x.png', '/a/./x.png'])}")
    check('空串忽略', video_core.unique_snapshot_paths(['', '/a/x.png'])
          == ['/a/x.png'])
    check('空列表', video_core.unique_snapshot_paths([]) == [])


# ---------------------------------------------------------------- 按钮

def test_button_exists():
    section('按钮: 存在 + 初始状态')
    dlg = video_qt.SnapshotDialog(None, [make_info()], '/tmp/x')
    try:
        check('有「清除截图」按钮',
              any(b.text() == '清除截图'
                  for b in dlg.findChildren(type(dlg.clear_btn))),
              f"got={[b.text() for b in dlg.findChildren(type(dlg.clear_btn))]}")
        # 按钮的先后看**布局里的次序** —— 窗口没 show 过, x() 全是 0
        row1 = dlg.layout().itemAt(0).layout()
        check('按钮在「开始截图」左边',
              row1.indexOf(dlg.clear_btn) < row1.indexOf(dlg.action_btn),
              f"clear={row1.indexOf(dlg.clear_btn)} "
              f"action={row1.indexOf(dlg.action_btn)}")
        check('初始是灰的 (界面上没截图)', not dlg.clear_btn.isEnabled())
    finally:
        dlg.close()


def test_button_enabled_rules():
    section('按钮: 什么时候能点')
    out = mkdtemp()
    dlg = video_qt.SnapshotDialog(None, [make_info()], out)
    try:
        dlg.model.set_time_tokens(['00:00:07:24'])
        dlg._sync_columns()
        path = make_png(os.path.join(out, 'a_t1_00-00-07-24.png'))
        fill_done(dlg, [(0, 2, path)])
        dlg._update_clear_btn()
        check('有截图 → 能点', dlg.clear_btn.isEnabled())
        # 抽帧中 → 不给点
        dlg._state = 'running'
        dlg._update_clear_btn()
        check('抽帧中 → 灰', not dlg.clear_btn.isEnabled())
        # 「查看已有截图」加载中 → 不给点
        dlg._state = 'done'
        dlg._loading_existing = True
        dlg._update_clear_btn()
        check('加载已有截图中 → 灰', not dlg.clear_btn.isEnabled())
        dlg._loading_existing = False
        dlg._update_clear_btn()
        check('加载完了 → 又能点', dlg.clear_btn.isEnabled())
    finally:
        dlg.close()


# ---------------------------------------------------------------- 清除

def test_clear_yes():
    section('清除: 确认后删文件 + 清界面')
    out = mkdtemp()
    dlg = video_qt.SnapshotDialog(None, [make_info()], out)
    try:
        dlg.model.set_time_tokens(['00:00:07:24', '00:00:08:00'])
        dlg._sync_columns()
        p1 = make_png(os.path.join(out, 'a_t1_00-00-07-24.png'))
        p2 = make_png(os.path.join(out, 'a_t2_00-00-08-00.png'))
        fill_done(dlg, [(0, 2, p1), (0, 3, p2)])
        dlg._state = 'done'
        dlg._update_clear_btn()

        confirm_answer['value'] = QMessageBox.Yes
        msgbox_log.clear()
        dlg._on_clear_snapshots()

        check('弹了确认框',
              any(k == 'question' for k, _t, _x in msgbox_log),
              f"got={msgbox_log}")
        check('文件被删了', not os.path.exists(p1) and not os.path.exists(p2))
        check('格子回到「待抽」(idle)',
              dlg.model.cell_state(0, 2).status == 'idle'
              and dlg.model.cell_state(0, 3).status == 'idle',
              f"got={dlg.model.cell_state(0, 2)}")
        check('缩略图缓存清了',
              dlg.delegate.thumbnail(0, 2) is None
              and dlg.delegate.thumbnail(0, 3) is None)
        check('进度条归零', dlg.progress.value() == 0,
              f"got={dlg.progress.value()}")
        check('清完按钮变灰', not dlg.clear_btn.isEnabled())
        check('状态行报已清除', '已清除' in dlg.status_label.text(),
              f"got={dlg.status_label.text()!r}")
    finally:
        dlg.close()


def test_clear_declined():
    section('清除: 点了「否」什么都别动')
    out = mkdtemp()
    dlg = video_qt.SnapshotDialog(None, [make_info()], out)
    try:
        dlg.model.set_time_tokens(['00:00:07:24'])
        dlg._sync_columns()
        path = make_png(os.path.join(out, 'a_t1_00-00-07-24.png'))
        fill_done(dlg, [(0, 2, path)])
        dlg._state = 'done'

        confirm_answer['value'] = QMessageBox.No
        dlg._on_clear_snapshots()
        check('文件还在', os.path.exists(path))
        check('格子还是 done', dlg.model.cell_state(0, 2).status == 'done')
        check('缩略图还在', dlg.delegate.thumbnail(0, 2) is not None)
    finally:
        dlg.close()


def test_clear_only_shown():
    section('清除: 只删界面上显示的那些')
    out = mkdtemp()
    dlg = video_qt.SnapshotDialog(None, [make_info()], out)
    try:
        dlg.model.set_time_tokens(['00:00:07:24'])
        dlg._sync_columns()
        shown = make_png(os.path.join(out, 'a_t1_00-00-07-24.png'))
        # 目录里另有别的图片: 别的视频 / 别的时间点 / 不合规的名字
        other = make_png(os.path.join(out, 'b_t1_00-00-07-24.png'))
        old = make_png(os.path.join(out, 'a_t2_00-00-08-00.png'))
        junk = make_png(os.path.join(out, 'not-a-snapshot.png'))
        fill_done(dlg, [(0, 2, shown)])
        dlg._state = 'done'

        confirm_answer['value'] = QMessageBox.Yes
        dlg._on_clear_snapshots()
        check('显示的那张被删', not os.path.exists(shown))
        check('别的视频的还在', os.path.exists(other))
        check('别的时间点的还在', os.path.exists(old))
        check('不合规名字的还在', os.path.exists(junk))
    finally:
        dlg.close()


def test_clear_same_file_twice():
    section('清除: 同一个文件被两格指着 → 只删一次')
    out = mkdtemp()
    # 两个视频的元数据 title 撞车 → 抽帧写出的文件名相同 → 两格指同一个文件
    dlg = video_qt.SnapshotDialog(
        None, [make_info('same.mp4', '/x/1.mp4'),
               make_info('same.mp4', '/x/2.mp4')], out)
    try:
        dlg.model.set_time_tokens(['00:00:07:24', '00:00:08:00'])
        dlg._sync_columns()
        path = make_png(os.path.join(out, 'same_t1_00-00-07-24.png'))
        fill_done(dlg, [(0, 2, path), (1, 2, path)])
        dlg._state = 'done'

        confirm_answer['value'] = QMessageBox.Yes
        dlg._on_clear_snapshots()
        check('文件被删了', not os.path.exists(path))
        check('两格都回到 idle',
              dlg.model.cell_state(0, 2).status == 'idle'
              and dlg.model.cell_state(1, 2).status == 'idle')
        check('没有失败项 (重复删不算失败)',
              '失败' not in dlg.status_label.text(),
              f"got={dlg.status_label.text()!r}")
    finally:
        dlg.close()


def test_clear_leaves_failed_and_empty():
    section('清除: 失败格与空白格不动')
    out = mkdtemp()
    dlg = video_qt.SnapshotDialog(None, [make_info()], out)
    try:
        dlg.model.set_time_tokens(['00:00:07:24', '00:00:08:00',
                                   '00:01:00:00'])
        dlg._sync_columns()
        path = make_png(os.path.join(out, 'a_t1_00-00-07-24.png'))
        fill_done(dlg, [(0, 2, path)])
        dlg.model.mark_failed(0, 3)      # 抽帧失败 (红 ×)
        dlg.model.mark_empty(0, 4)       # 没有旧图 (空白)
        dlg._state = 'done'

        confirm_answer['value'] = QMessageBox.Yes
        dlg._on_clear_snapshots()
        check('失败格还是 failed', dlg.model.cell_state(0, 3).status == 'failed',
              f"got={dlg.model.cell_state(0, 3).status}")
        check('空白格还是 empty', dlg.model.cell_state(0, 4).status == 'empty',
              f"got={dlg.model.cell_state(0, 4).status}")
        check('成功的格回到 idle', dlg.model.cell_state(0, 2).status == 'idle')
    finally:
        dlg.close()


def test_clear_while_paused():
    section('清除: 暂停中点它 → 这一轮结束')
    win = video_qt.MainWindow()
    out = mkdtemp()
    calls = []
    real = video_qt.extract_frame

    def fake_extract(path, seconds, out_path):
        calls.append(os.path.basename(out_path))
        # 慢一点 —— 抽太快的话, 等第一格抽完时整轮已经跑完了,
        # 「点一下暂停」就变成了「再起一轮」。
        time.sleep(0.2)
        with open(out_path, 'wb') as f:
            f.write(fake_png_bytes())
        return True

    video_qt.extract_frame = fake_extract
    try:
        # 两个视频 × 三个时间点 = 6 格, 每格 0.2 秒 → 整轮 1.2 秒
        dlg = video_qt.SnapshotDialog(
            win, [make_info('a.mp4', '/x/a.mp4'),
                  make_info('b.mp4', '/x/b.mp4')], out)
        dlg._on_action_btn()
        # 等第一格抽完
        deadline = time.time() + 10
        while dlg._cells_processed < 1 and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        check('至少抽完一格', dlg._cells_processed >= 1,
              f"got={dlg._cells_processed}")
        # 暂停
        dlg.action_btn.click()
        check('状态是 paused', dlg._state == 'paused', f"got={dlg._state!r}")
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
        # 暂停时「清除截图」是可点的
        check('暂停时能点「清除截图」', dlg.clear_btn.isEnabled())

        confirm_answer['value'] = QMessageBox.Yes
        dlg._on_clear_snapshots()
        check('文件被删了',
              not any(os.path.exists(os.path.join(out, c)) for c in calls),
              f"calls={calls}")

        # 等 worker 收尾 ('done' 消息由 100ms 的轮询定时器处理)
        deadline = time.time() + 10
        while dlg._state == 'paused' and time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
        app.processEvents()
        check('这一轮结束了 (状态回 idle)', dlg._state == 'idle',
              f"got={dlg._state!r}")
        check('按钮回到「开始截图」', dlg.action_btn.text() == '开始截图',
              f"got={dlg.action_btn.text()!r}")
        check('状态行说的是「已清除」不是「已取消」',
              '已清除' in dlg.status_label.text(),
              f"got={dlg.status_label.text()!r}")
        check('worker 线程退出了',
              dlg._worker_thread is None or not dlg._worker_thread.is_alive())
        dlg.close()
    finally:
        video_qt.extract_frame = real
        win.close()


def fake_png_bytes(width: int = 64, height: int = 36) -> bytes:
    """一张纯色 PNG —— 缩略图要从磁盘读, 得是真图片。"""
    tmp = os.path.join(mkdtemp(), 'x.png')
    img = QImage(width, height, QImage.Format_RGB32)
    img.fill(QColor('red'))
    img.save(tmp, 'PNG')
    with open(tmp, 'rb') as f:
        data = f.read()
    os.remove(tmp)
    return data


def main() -> int:
    orig_warning = QMessageBox.warning
    orig_critical = QMessageBox.critical
    orig_question = QMessageBox.question
    QMessageBox.warning = stub_msgbox('warning')
    QMessageBox.critical = stub_msgbox('critical')
    QMessageBox.question = stub_question
    try:
        test_unique_paths()
        test_button_exists()
        test_button_enabled_rules()
        test_clear_yes()
        test_clear_declined()
        test_clear_only_shown()
        test_clear_same_file_twice()
        test_clear_leaves_failed_and_empty()
        test_clear_while_paused()
    finally:
        QMessageBox.warning = orig_warning
        QMessageBox.critical = orig_critical
        QMessageBox.question = orig_question
        cleanup_tmpdirs()

    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
