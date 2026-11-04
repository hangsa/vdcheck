"""「查看已有截图」的行为断言。

对照 docs/产品文档.md §3.5 (定点截图) 与 2026-11-03 新增的需求:

    点「查看已有截图」→ 扫截图地址里**符合命名规则**的 PNG → 按
    「视频标题 + 时间点」匹配回填到网格; 匹配不上的格子**留空**;
    「开始截图」的行为一个字都不改 (仍然抽帧、仍然覆盖旧文件)。

跑法:
    QT_QPA_PLATFORM=offscreen python docs/superpowers/specs/2026-11-03-snapshot-existing-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import re
import shutil
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
sys.path.insert(0, _ROOT)

_tmpdirs: list[str] = []


def mkdtemp(prefix: str = 'vdcheck_') -> str:
    """建临时目录, 记下来, 退出时清掉。"""
    path = tempfile.mkdtemp(prefix=prefix)
    _tmpdirs.append(path)
    return path


def cleanup_tmpdirs() -> None:
    for path in _tmpdirs:
        shutil.rmtree(path, ignore_errors=True)
    _tmpdirs.clear()


# 没有显示环境时 Qt 用 offscreen 平台 (会打一条字体族警告到 stderr, 不在范围)。
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QMessageBox, QPushButton, QStyleOptionViewItem,
)

# QApplication 必须**先于** video_qt 建好 (ui_font() 惰性求值时要拷贝当前
# app 的默认字体, app 还不存在时字体族是空的 → QFontMetrics 直接 abort)。
app = QApplication.instance() or QApplication(sys.argv)

import video_core  # noqa: E402
import video_qt  # noqa: E402
from video_core import VideoInfo  # noqa: E402

_failures: list[str] = []
# 弹框记录 (模块级 —— main 与 _run 都要用)
msgbox_log: list[tuple[str, str, str]] = []
real_extract = video_qt.extract_frame
real_enlarge = video_qt.EnlargeDialog


def stub_msgbox(kind: str):
    """造一个「记下来但不阻塞」的 QMessageBox 静态方法。"""
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


def make_info(title='a.mp4', full_path='/x/a.mp4') -> VideoInfo:
    return VideoInfo(
        rel_path='./', title=title, resolution='1920x1080',
        frame_rate='30.00', bitrate_kbps=32000.0, video_codec='h264',
        audio_codec='aac', audio_channels='2', audio_sample_rate='48.0',
        duration='00:01:00', file_size='1.0 GB', is_passing=True,
        sample_rate_passing=True, bitrate_std=30000.0, sample_rate_std=48.0,
        full_path=full_path, container_format='mp4',
        frame_rate_fps=30.0, sample_rate_khz=48.0,
        # 时长 (秒) —— 逆时间点算实际时刻要用 (默认第 3 个是逆的)
        duration_seconds=3600.0)


def fake_png_bytes(width: int = 640, height: int = 360,
                   color='red') -> bytes:
    """一张纯色的 PNG —— 假装是抽出来的 640px 原图。"""
    img = QImage(width, height, QImage.Format_RGB32)
    img.fill(QColor(color))
    path = os.path.join(mkdtemp(), 'x.png')
    img.save(path, 'PNG')
    with open(path, 'rb') as f:
        data = f.read()
    os.remove(path)
    return data


def write_png(directory: str, name: str, color='red') -> str:
    path = os.path.join(directory, name)
    with open(path, 'wb') as f:
        f.write(fake_png_bytes(color=color))
    return path


def count_ink(img: QImage) -> int:
    """数一数有多少像素**与背景色不同** (背景 = 左上角那个像素)。

    空格子 → 0; 画了 '…' 或 '×' 或缩略图 → 明显 > 0。
    """
    if img.isNull() or img.width() == 0 or img.height() == 0:
        return 0
    bg = img.pixelColor(0, 0)
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if abs(c.red() - bg.red()) > 30 or abs(c.green() - bg.green()) > 30 \
                    or abs(c.blue() - bg.blue()) > 30:
                n += 1
    return n


def render_cell(model, delegate, row: int, col: int,
                size=(120, 80)) -> QImage:
    """把某个 cell 用 delegate 画到一张图上, 返回 QImage。"""
    pm = QPixmap(size[0], size[1])
    pm.fill(Qt.white)
    painter = QPainter(pm)
    opt = QStyleOptionViewItem()
    opt.rect = pm.rect()
    delegate.paint(painter, opt, model.index(row, col))
    painter.end()
    return pm.toImage()


def pump_until(predicate, timeout: float = 20.0) -> bool:
    """泵事件循环直到 predicate 为真 (worker 在另一个线程里跑)。"""
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    app.processEvents()
    return bool(predicate())


def view_existing(dlg) -> None:
    """点「查看已有截图」并**等它跑完**。

    这一步现在是**后台线程** (2026-11-03 加的加载进度) —— 点完不会立刻
    填好网格, 得泵事件循环等它结束, 断言才有东西可看。
    """
    dlg._on_view_existing()
    pump_until(lambda: not dlg._loading_existing, timeout=30)


def find_button(dlg, text: str):
    """在弹窗里按文字找按钮。"""
    for btn in dlg.findChildren(QPushButton):
        if btn.text() == text:
            return btn
    return None


# ======================================================================
# A. 纯逻辑 (video_core)
# ======================================================================

def test_core() -> None:
    section("核心: 截图文件名解析")
    p = video_core.parse_snapshot_filename
    check("正常名字", p('a_t1_00-00-07-24.png') == ('a', '00:00:07:24'),
          f"got={p('a_t1_00-00-07-24.png')!r}")
    check("标题里自带 _t1_ (取最右一次)",
          p('a_t1_t2_00-00-07-24.png') == ('a_t1', '00:00:07:24'),
          f"got={p('a_t1_t2_00-00-07-24.png')!r}")
    check("标题里有连字符",
          p('my-video_t3_00-01-00-00.png') == ('my-video', '00:01:00:00'),
          f"got={p('my-video_t3_00-01-00-00.png')!r}")
    check("帧号可以多位",
          p('a_t1_00-00-07-100.png') == ('a', '00:00:07:100'),
          f"got={p('a_t1_00-00-07-100.png')!r}")
    check("中文标题", p('中文片名_t2_00-00-08-00.png') == ('中文片名', '00:00:08:00'),
          f"got={p('中文片名_t2_00-00-08-00.png')!r}")
    check("不是截图命名 → None", p('random.png') is None)
    check("少了 _tN_ → None", p('a_00-00-07-24.png') is None)
    check("扩展名不对 → None", p('a_t1_00-00-07-24.jpg') is None)
    check("只有一半 → None", p('a_t1.png') is None)

    section("核心: 写↔读往返 (生成端与解析端必须对称)")
    # 生成端在 video_qt._snapshot_worker:
    #     f"{base}_t{idx+1}_{time_token_to_string(token)}.png"
    # 这一节穷举一批标题 × 时间点, 写出来再解析回去 —— 两边不对称
    # (比如标题里带 '_t' / '-' / 空格) 就会在这里露馅。
    bad = []
    for title in ['a', 'a_t1', 'a_t1_t2', 'my-video', '片 名', 'A_1',
                  'x_t9', '2024_01_01', 'a-b_c']:
        for token in ['00:00:07:24', '00:00:08:00', '00:01:00:00',
                      '01:00:00:00', '00:00:07:100']:
            base = title
            name = f"{base}_t1_{token.replace(':', '-')}.png"
            got = p(name)
            if got != (base, token):
                bad.append((name, got, (base, token)))
    check("写出来的名字都能原样解析回去",
          not bad, f"got={bad[:3]}")
    # 序号那一段 (t1 / t2 / t12) 不参与匹配 —— 解析出来也该原样
    check("序号段是数字", p('a_t12_00-00-07-24.png') == ('a', '00:00:07:24'))

    section("核心: 扫已有截图")
    d = mkdtemp()
    write_png(d, 'a_t1_00-00-07-24.png')
    write_png(d, 'a_t2_00-01-00-00.png')
    write_png(d, 'noise.png')
    write_png(d, 'b_t1_乱写.png')
    write_png(d, 'a_t3_00-00-08-00.txt')
    sub = os.path.join(d, 'sub')
    os.makedirs(sub)
    write_png(sub, 'a_t9_00-09-09-09.png')
    snaps = video_core.scan_existing_snapshots(d)
    check("只收合规的 PNG (2 张)",
          len(snaps) == 2, f"got={[s.path for s in snaps]}")
    check("base / token 拆对",
          sorted((s.base, s.token) for s in snaps)
          == [('a', '00:00:07:24'), ('a', '00:01:00:00')],
          f"got={sorted((s.base, s.token) for s in snaps)}")
    check("不递归子目录",
          all('sub' not in s.path for s in snaps))
    check("目录不存在 → 空列表",
          video_core.scan_existing_snapshots(os.path.join(d, 'nope')) == [])
    check("空目录 → 空列表", video_core.scan_existing_snapshots(mkdtemp()) == [])

    section("核心: 按 base 匹配")
    S = video_core.SnapshotFile
    f1 = S('a', '00:00:07:24', '/p1.png', 100.0)
    f2 = S('a', '00:00:07:24', '/p2.png', 200.0)
    f3 = S('b', '00:00:07:24', '/p3.png', 300.0)
    m = video_core.match_snapshots([f1, f2, f3], 'a')
    check("只留下 base 匹配的", set(m) == {'00:00:07:24'}, f"got={set(m)}")
    check("同一时间点多张 → 取 mtime 最新的",
          m['00:00:07:24'].path == '/p2.png',
          f"got={m['00:00:07:24'].path}")
    check("别的视频的图不串行",
          video_core.match_snapshots([f1, f2, f3], 'zzz') == {})

    section("核心: 时间点排序")
    key = video_core.time_token_sort_key
    toks = ['00:01:00:00', '00:00:08:00', '00:00:07:24', '00:10:00:00']
    check("按数值排序, 不是按字符串",
          sorted(toks, key=key)
          == ['00:00:07:24', '00:00:08:00', '00:01:00:00', '00:10:00:00'],
          f"got={sorted(toks, key=key)}")


# ======================================================================
# B. 界面层 (video_qt)
# ======================================================================

def test_dialog() -> None:
    win = video_qt.MainWindow()

    # ---- 目录里已有三张图, 分属两个视频 ----
    out = mkdtemp()
    p_a1 = write_png(out, 'a_t1_00-00-07-24.png', 'red')
    p_a2 = write_png(out, 'a_t2_00-00-08-00.png', 'green')
    p_b1 = write_png(out, 'b_t1_00-00-07-24.png', 'blue')
    write_png(out, 'c_t1_00-00-07-24.png', 'yellow')   # 不在本次选中范围

    files = [make_info('a.mp4'), make_info('b.mp4')]
    dlg = video_qt.SnapshotDialog(win, files, out)

    section("界面: 新按钮")
    btn = find_button(dlg, '查看已有截图')
    check("有「查看已有截图」按钮", btn is not None)
    check("按钮在第一行 (和「开始截图」同一行)",
          btn is not None and btn.parent() is dlg
          and dlg.action_btn.parent() is dlg)
    check("「开始截图」按钮还在", find_button(dlg, '开始截图') is not None)

    section("界面: 回填已有截图")
    before = {n: (os.path.getmtime(os.path.join(out, n)),
                   open(os.path.join(out, n), 'rb').read())
              for n in os.listdir(out)}
    view_existing(dlg)

    check("列数 = 2 + 目录里的时间点数 (2)",
          dlg.model.columnCount() == 4, f"got={dlg.model.columnCount()}")
    check("时间点按时间排序",
          [dlg.model.headerData(c, Qt.Horizontal) for c in (2, 3)]
          == ['T1: 00-00-07-24', 'T2: 00-00-08-00'],
          f"got={[dlg.model.headerData(c, Qt.Horizontal) for c in (2, 3)]}")
    check("a 的第 1 格 = done",
          dlg.model.cell_state(0, 2).status == 'done',
          f"got={dlg.model.cell_state(0, 2)}")
    check("a 的第 2 格 = done",
          dlg.model.cell_state(0, 3).status == 'done',
          f"got={dlg.model.cell_state(0, 3)}")
    check("a 的第 2 格指向磁盘上那张旧图",
          dlg.model.cell_state(0, 3).image_path == p_a2,
          f"got={dlg.model.cell_state(0, 3).image_path}")
    check("a 的缩略图来自磁盘上的旧图",
          dlg.model.cell_state(0, 2).image_path == p_a1,
          f"got={dlg.model.cell_state(0, 2).image_path}")
    check("b 命中第 1 格 (同名时间点)",
          dlg.model.cell_state(1, 2).status == 'done'
          and dlg.model.cell_state(1, 2).image_path == p_b1,
          f"got={dlg.model.cell_state(1, 2)}")
    check("b 没有第 2 个时间点的图 → empty",
          dlg.model.cell_state(1, 3).status == 'empty',
          f"got={dlg.model.cell_state(1, 3)}")
    check("不匹配的 c 不占格子 (只影响行数)",
          dlg.model.rowCount() == 2, f"got={dlg.model.rowCount()}")

    section("界面: 落空的格子是空白, 不是 '…'")
    ink_empty = count_ink(render_cell(dlg.model, dlg.delegate, 1, 3))
    check("empty 格画出来没有墨迹", ink_empty == 0, f"ink={ink_empty}")
    ink_done = count_ink(render_cell(dlg.model, dlg.delegate, 0, 2))
    check("done 格画出来有缩略图", ink_done > 0, f"ink={ink_done}")
    # 对照: 'idle' 状态画的是灰色 '…' → 有墨迹 (证明上面那条不是恒真)
    idle_model = video_qt.SnapshotGridModel(files)
    idle_model.set_time_tokens(['00:00:07:24'])
    ink_idle = count_ink(render_cell(idle_model, dlg.delegate, 0, 2))
    check("对照: idle 格画 '…' 有墨迹 (证明上一条有牙)",
          ink_idle > 0, f"ink={ink_idle}")

    section("界面: 旧图没被动过")
    after = {n: (os.path.getmtime(os.path.join(out, n)),
                  open(os.path.join(out, n), 'rb').read())
             for n in os.listdir(out)}
    check("文件内容与 mtime 都没变", before == after)

    section("界面: 点回填的格子能开放大窗口")
    opened = []

    class FakeEnlarge:
        def __init__(self, parent, image_path, title):
            opened.append((image_path, title))

        def show(self):
            pass

    video_qt.EnlargeDialog = FakeEnlarge
    try:
        dlg._on_cell_clicked(dlg.model.index(0, 2))
    finally:
        video_qt.EnlargeDialog = real_enlarge
    check("放大窗口拿到的是磁盘上的旧图",
          len(opened) == 1 and opened[0][0] == p_a1, f"got={opened}")

    section("界面: 时间点输入框不被改")
    entries_before = [e.text() for e in dlg._tp_entries]
    view_existing(dlg)
    check("输入框内容原样保留 (列取自目录, 不取自输入框)",
          [e.text() for e in dlg._tp_entries] == entries_before,
          f"got={[e.text() for e in dlg._tp_entries]}")

    section("界面: 状态栏")
    check("状态栏说明是已有截图",
          '已有截图' in dlg.status_label.text(),
          f"got={dlg.status_label.text()!r}")

    # ---- 目录不存在: 不建目录, 只提示 ----
    section("界面: 截图地址不存在")
    missing = os.path.join(mkdtemp(), 'nope')
    dlg2 = video_qt.SnapshotDialog(win, files, missing)
    view_existing(dlg2)
    check("**没有**建目录", not os.path.exists(missing))
    check("状态栏提示了", '不存在' in dlg2.status_label.text(),
          f"got={dlg2.status_label.text()!r}")
    check("网格没炸 (仍是 2 列)", dlg2.model.columnCount() == 2,
          f"got={dlg2.model.columnCount()}")

    # ---- 目录里没有合规图片 ----
    section("界面: 目录里没有符合命名规则的图片")
    empty = mkdtemp()
    write_png(empty, '随便一张.png')
    dlg3 = video_qt.SnapshotDialog(win, files, empty)
    view_existing(dlg3)
    check("状态栏提示了", '没有' in dlg3.status_label.text(),
          f"got={dlg3.status_label.text()!r}")
    check("网格没炸 (仍是 2 列)", dlg3.model.columnCount() == 2,
          f"got={dlg3.model.columnCount()}")

    # ---- 「开始截图」行为一个字都没改 ----
    section("「开始截图」行为不变 (仍然抽帧、仍然覆盖旧文件)")
    out2 = mkdtemp()
    old = write_png(out2, 'a_t1_00-00-07-24.png', 'red')
    old_bytes = open(old, 'rb').read()
    dlg4 = video_qt.SnapshotDialog(win, [make_info('a.mp4')], out2)
    view_existing(dlg4)
    check("先看已有截图: 第 1 格是 done",
          dlg4.model.cell_state(0, 2).status == 'done',
          f"got={dlg4.model.cell_state(0, 2)}")

    written = []

    def fake_extract(input_path, seconds, output_path):
        written.append(output_path)
        with open(output_path, 'wb') as f:
            f.write(fake_png_bytes(color='blue'))
        return True

    video_qt.extract_frame = fake_extract
    try:
        dlg4._on_action_btn()
        finished = pump_until(lambda: dlg4._state != 'running', timeout=20)
    finally:
        video_qt.extract_frame = real_extract
    check("抽帧跑完了", finished, f"state={dlg4._state}")
    check("仍然真的调了 extract_frame", len(written) == 3,
          f"got={len(written)}")
    check("旧文件被覆盖 (内容变了)",
          open(old, 'rb').read() != old_bytes)
    check("所有格都变成 done",
          all(dlg4.model.cell_state(0, c).status == 'done'
              for c in range(2, 5)),
          f"got={[dlg4.model.cell_state(0, c) for c in range(2, 5)]}")

    section("抽帧进行中「查看已有截图」被禁用")
    dlg5 = video_qt.SnapshotDialog(win, [make_info('a.mp4')], out2)
    video_qt.extract_frame = fake_extract
    try:
        dlg5._on_action_btn()
        btn5 = find_button(dlg5, '查看已有截图')
        disabled = btn5 is not None and not btn5.isEnabled()
        pump_until(lambda: dlg5._state != 'running', timeout=20)
        enabled_again = btn5 is not None and btn5.isEnabled()
    finally:
        video_qt.extract_frame = real_extract
    check("抽帧中按钮禁用", disabled)
    check("跑完按钮恢复可用", enabled_again)


def test_version() -> None:
    """版本号守卫: 窗口标题与产品文档 §4.1 必须一致。

    之前**没有任何断言**盯着窗口标题 —— 版本号一抬, 产品文档就可能
    悄悄漂掉 (2026-11-03 抬 v4.1 时才发现)。这里把两者钉在一起。
    (2026-11-04 抬 v4.2 时, 期望值跟着抬 —— 守卫盯的是**三者一致**,
     不是某个具体版本号。)
    """
    section("版本号: 窗口标题 ↔ 产品文档 §4.1")
    win = video_qt.MainWindow()
    title = win.windowTitle()
    check("窗口标题是 v4.3", title == '视频检测v4.3', f"got={title!r}")
    with open(os.path.join(_ROOT, 'docs', '产品文档.md'), encoding='utf-8') as f:
        doc = f.read()
    m = re.search(r'窗口标题 `([^`]+)`', doc)
    check("产品文档 §4.1 里的窗口标题与代码一致",
          m is not None and m.group(1) == title,
          f"doc={m.group(1) if m else None!r} code={title!r}")
    m2 = re.search(r'^版本: (v[\d.]+)', doc, re.M)
    check("产品文档版本头是 v4.3",
          m2 is not None and m2.group(1) == 'v4.3',
          f"got={m2.group(1) if m else None!r}")


def main() -> int:
    orig_warning = QMessageBox.warning
    orig_critical = QMessageBox.critical
    orig_question = QMessageBox.question
    QMessageBox.warning = stub_msgbox('warning')
    QMessageBox.critical = stub_msgbox('critical')
    QMessageBox.question = stub_msgbox('question')
    try:
        test_core()
        test_dialog()
        test_version()
    finally:
        QMessageBox.warning = orig_warning
        QMessageBox.critical = orig_critical
        QMessageBox.question = orig_question
        video_qt.extract_frame = real_extract
        video_qt.EnlargeDialog = real_enlarge
        cleanup_tmpdirs()

    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
