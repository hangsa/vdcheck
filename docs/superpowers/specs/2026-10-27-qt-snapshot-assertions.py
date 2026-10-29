"""Qt 定点截图弹窗 + 放大窗口 的行为断言。

对照 docs/产品文档.md §3.5 / §4.3 / §6 与
docs/superpowers/plans/2026-10-27-pyside6-qtableview.md 的 Task 6。

跑法:
    QT_QPA_PLATFORM=offscreen python docs/superpowers/specs/2026-10-27-qt-snapshot-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import ast
import os
import shutil
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

from PySide6.QtCore import QBuffer, QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor, QFont, QFontMetrics, QImage, QKeyEvent, QMouseEvent, QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QMessageBox, QPushButton, QStyleOptionViewItem,
    QTableView,
)

# QApplication 必须**先于** video_qt 建好: ui_font() 惰性求值时会拷贝当前
# app 的默认字体, app 还不存在时字体族是空的 (family=''), 字宽度量整套
# 偏掉, QFontMetrics(那个字体) 直接 abort 进程。
app = QApplication.instance() or QApplication(sys.argv)

import video_qt  # noqa: E402
from video_core import VideoInfo  # noqa: E402

REQUIRED = ('SnapshotDialog', 'EnlargeDialog', 'SnapshotGridModel',
            'SnapshotDelegate', 'TimeTokenEdit')

_failures: list[str] = []

# 弹框记录 (模块级 —— _run 与 main 都要用)
msgbox_log: list[tuple[str, str, str]] = []
# question 弹框的回答 (默认「否」)
msgbox_answer: dict[str, int] = {'question': QMessageBox.No}
# 被 patch 掉的原始对象 —— 用完要还原
real_extract = video_qt.extract_frame
real_enlarge = video_qt.EnlargeDialog


def stub_msgbox(kind: str):
    """造一个「记下来但不阻塞」的 QMessageBox 静态方法。

    真实的 QMessageBox 会模态地转事件循环 —— 断言脚本里等它关闭就卡死了。
    """
    def _stub(parent, title, text, *args, **kwargs):
        msgbox_log.append((kind, title, text))
        if kind == 'question':
            return msgbox_answer['question']
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


def fake_png_bytes(width: int = 640, height: int = 360) -> bytes:
    """一张纯红的 PNG —— 假装是抽出来的 640px 原图。

    在**主线程**里做好字节, worker 只负责把字节写盘。
    """
    img = QImage(width, height, QImage.Format_RGB32)
    img.fill(QColor('red'))
    buf = QBuffer()
    buf.open(QBuffer.WriteOnly)
    img.save(buf, 'PNG')
    return bytes(buf.data())


def render_cell(model, delegate, row: int, col: int,
                size=(80, 40)) -> QImage:
    """把某个 cell 用 delegate 画到一张白底图上, 返回 QImage。"""
    pm = QPixmap(size[0], size[1])
    pm.fill(Qt.white)
    painter = QPainter(pm)
    opt = QStyleOptionViewItem()
    opt.rect = pm.rect()
    delegate.paint(painter, opt, model.index(row, col))
    painter.end()
    return pm.toImage()


def render_real_cell(view, delegate, row: int, col: int) -> QImage:
    """用**真实 view 的真实 cell 矩形**渲染一个 cell。

    render_cell() 用的是**合成**的 80x40 rect —— opt.rect 是那张图自己的
    rect, **从不使用真实行高** → 缩略图永远画得下, 于是「行高把缩略图
    压成 53x30」这个 bug 从来抓不到 (2026-10-29 用户反馈暴露的正是它)。

    这里用 view.visualRect(index) —— 真正显示在屏幕上的那个矩形,
    行高、列宽都是真的。

    矩形**平移到原点**: 画到一张新图上时, cell 在视口里的偏移 (x>0)
    会把缩略图画到图外面去。平移只动位置, 不动尺寸。
    """
    idx = view.model().index(row, col)
    rect = view.visualRect(idx)
    rect.moveTo(QPoint(0, 0))
    if rect.width() <= 0 or rect.height() <= 0:
        return QImage()
    pm = QPixmap(rect.width(), rect.height())
    pm.fill(Qt.white)
    painter = QPainter(pm)
    opt = QStyleOptionViewItem()
    opt.rect = rect
    delegate.paint(painter, opt, idx)
    painter.end()
    return pm.toImage()


def count_hues(img: QImage) -> tuple[int, int, int]:
    """数一下图里有多少「蓝味 / 红味 / 中性」的墨迹像素。

    按色相判 (b >> r / r >> b), 不按亮度 —— 抗锯齿会混出一堆中间色,
    按亮度判会漏掉字芯。白色背景 (255,255,255) 三色都不沾。
    """
    blue = red = neutral = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.blue() > c.red() + 40 and c.blue() > c.green() + 40:
                blue += 1
            elif c.red() > c.green() + 40 and c.red() > c.blue() + 40:
                red += 1
            elif abs(c.red() - c.green()) < 30 and abs(c.green() - c.blue()) < 30 \
                    and c.lightness() < 200:
                neutral += 1
    return blue, red, neutral


def red_ink_bbox(img: QImage) -> tuple[int, int, int, int]:
    """红墨迹的外接矩形 (x, y, w, h)。没有红墨迹时返回 (0, 0, 0, 0)。"""
    xs, ys = [], []
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.red() > c.green() + 40 and c.red() > c.blue() + 40:
                xs.append(x)
                ys.append(y)
    if not xs:
        return (0, 0, 0, 0)
    return (min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


def render_glyph(text: str, font, color=QColor('red'), size=(80, 40)) -> QImage:
    """对照组: 用**指定的字体**画一个字, 返回图。

    用来跟 delegate 画出来的失败 '×' 比 —— 断言它确实是 14pt 粗体。
    """
    pm = QPixmap(size[0], size[1])
    pm.fill(Qt.white)
    painter = QPainter(pm)
    painter.setFont(font)
    painter.setPen(color)
    painter.drawText(pm.rect(), Qt.AlignCenter, text)
    painter.end()
    return pm.toImage()


def _img_attrs(obj) -> list[str]:
    """对象上所有「装着一张图」的属性名 (QPixmap / QImage)。"""
    return sorted(name for name, val in vars(obj).items()
                  if isinstance(val, (QPixmap, QImage)))


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


def x_for_char(edit, index: int) -> int:
    """找出一个 x —— 按下鼠标时光标正好落在第 index 个字符上。

    用 QLineEdit **自己**的命中测试反推 (不自己算像素宽度: 不同平台的
    QLineEdit 内边距不一样, 算出来会偏)。
    """
    for x in range(0, max(edit.width(), 40)):
        edit.setCursorPosition(0)
        _press(edit, x)
        if edit.cursorPosition() == index:
            return x
    raise AssertionError(f'找不到落在第 {index} 个字符上的 x')


def _press(edit, x: int) -> None:
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(x, 8),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(edit, ev)


def _double_click(edit, x: int) -> None:
    ev = QMouseEvent(QEvent.Type.MouseButtonDblClick, QPointF(x, 8),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(edit, ev)


def module_makedirs_owners() -> set[tuple[str | None, str]]:
    """整个 video_qt.py 里 os.makedirs / mkdir 的调用点 → (类名, 函数名)。

    **整个模块**扫 —— 只扫 MainWindow 上的方法抓不到弹窗类自己建目录的
    情况 (那正是要防的)。
    """
    with open(video_qt.__file__, encoding='utf-8') as f:
        tree = ast.parse(f.read())
    owners: set[tuple[str | None, str]] = set()

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.cls: str | None = None
            self.func: str | None = None

        def visit_ClassDef(self, node):
            prev, self.cls = self.cls, node.name
            self.generic_visit(node)
            self.cls = prev

        def visit_FunctionDef(self, node):
            prev, self.func = self.func, node.name
            self.generic_visit(node)
            self.func = prev

        def visit_Call(self, node):
            func = node.func
            if isinstance(func, ast.Attribute) \
                    and func.attr in ('makedirs', 'mkdir'):
                owners.add((self.cls, self.func or '<module>'))
            self.generic_visit(node)

    Visitor().visit(tree)
    return owners


def main() -> int:
    missing = [name for name in REQUIRED if not hasattr(video_qt, name)]
    if missing:
        print(f"✗ video_qt 缺少 Task 6 的实现: {missing}")
        return 1

    # ---- 把会弹框的静态方法换成「记下来不阻塞」 ----
    orig_warning = QMessageBox.warning
    orig_critical = QMessageBox.critical
    orig_question = QMessageBox.question

    QMessageBox.warning = stub_msgbox('warning')
    QMessageBox.critical = stub_msgbox('critical')
    QMessageBox.question = stub_msgbox('question')

    png_bytes = fake_png_bytes()

    def fake_extract(input_path, seconds, output_path):
        """假的 extract_frame: 写一张 640px 红 PNG 上去, 返回 True。"""
        with open(output_path, 'wb') as f:
            f.write(png_bytes)
        return True

    video_qt.extract_frame = fake_extract

    try:
        return _run()
    finally:
        QMessageBox.warning = orig_warning
        QMessageBox.critical = orig_critical
        QMessageBox.question = orig_question
        video_qt.extract_frame = real_extract
        video_qt.EnlargeDialog = real_enlarge
        cleanup_tmpdirs()


def _run() -> int:
    SnapshotDialog = video_qt.SnapshotDialog
    EnlargeDialog = video_qt.EnlargeDialog
    SnapshotGridModel = video_qt.SnapshotGridModel
    SnapshotDelegate = video_qt.SnapshotDelegate
    TimeTokenEdit = video_qt.TimeTokenEdit

    # ================================================================
    section("Task 6 的类都在 video_qt 里")
    check("SnapshotDialog / EnlargeDialog / SnapshotGridModel / "
          "SnapshotDelegate / TimeTokenEdit 都存在",
          all(hasattr(video_qt, n) for n in REQUIRED),
          f"got={[n for n in REQUIRED if not hasattr(video_qt, n)]}")

    # ================================================================
    section("默认时间点 / 「+」追加 / 无删除按钮")
    win = video_qt.MainWindow()
    dlg = SnapshotDialog(win, [make_info()], '/tmp/does/not/exist/snapshot')
    tokens = [e.text() for e in dlg._tp_entries]
    check("默认两个时间点 00:00:07:24 / 00:00:08:00",
          tokens == ['00:00:07:24', '00:00:08:00'], f"got={tokens}")
    dlg._add_time_token_entry('00:00:00:00')
    tokens = [e.text() for e in dlg._tp_entries]
    check("「+」追加的是 00:00:00:00",
          len(tokens) == 3 and tokens[2] == '00:00:00:00', f"got={tokens}")
    btn_texts = [b.text() for b in dlg.findChildren(QPushButton)]
    check("**没有删除按钮** (清空输入框即删除)",
          not any('删除' in t or '移除' in t or '-' == t for t in btn_texts),
          f"got={btn_texts}")
    check("只有一个「+」按钮",
          btn_texts.count('+') == 1, f"got={btn_texts}")

    # ================================================================
    section("截图地址: 默认 / 「浏览」套一层 snapshot / 不建目录")
    src_dir = mkdtemp(prefix='vdcheck_qt_snapsrc_')
    win2 = video_qt.MainWindow()
    win2._sync_derived_paths(src_dir)
    dlg2 = SnapshotDialog(win2, [make_info()], win2.snapshot_dest)
    check("截图地址默认值 = <源>/snapshot",
          dlg2.output_edit.text() == video_qt.snapshot_dest_for(src_dir),
          f"got={dlg2.output_edit.text()!r}")
    check("**默认地址这一步不建目录**",
          not os.path.isdir(dlg2.output_edit.text()),
          f"path={dlg2.output_edit.text()!r}")
    picked = mkdtemp(prefix='vdcheck_qt_snappick_')
    dlg2._ask_directory = lambda title: picked
    dlg2._browse_output_dir()
    check("「浏览」选的是**父目录** → 套一层 snapshot",
          dlg2.output_edit.text() == video_qt.snapshot_dest_for(picked),
          f"got={dlg2.output_edit.text()!r}")
    check("**「浏览」不建目录**",
          not os.path.isdir(dlg2.output_edit.text()),
          f"path={dlg2.output_edit.text()!r}")

    # ================================================================
    section("初始两列 → 起跑后按 N 个时间点重建列 / 表头文字")
    model = SnapshotGridModel([make_info()])
    check("初始只有序号 + 标题两列", model.columnCount() == 2,
          f"got={model.columnCount()}")
    check("表头: 序号 / 标题",
          model.headerData(0, Qt.Horizontal) == '序号'
          and model.headerData(1, Qt.Horizontal) == '标题',
          f"got={model.headerData(0, Qt.Horizontal)!r}/"
          f"{model.headerData(1, Qt.Horizontal)!r}")
    model.set_time_tokens(['00:00:07:24', '00:00:08:00'])
    check("起跑后按 2 个时间点重建 → 4 列", model.columnCount() == 4,
          f"got={model.columnCount()}")
    check("表头文字 T1: 00-00-07-24",
          model.headerData(2, Qt.Horizontal) == 'T1: 00-00-07-24',
          f"got={model.headerData(2, Qt.Horizontal)!r}")
    check("表头文字 T2: 00-00-08-00",
          model.headerData(3, Qt.Horizontal) == 'T2: 00-00-08-00',
          f"got={model.headerData(3, Qt.Horizontal)!r}")

    # ================================================================
    section("单元格三态 (像素级): 灰 … / 蓝 … / 红 × / 画图")
    delegate = SnapshotDelegate()
    model.set_time_tokens(['00:00:07:24'])
    blue, red, neutral = count_hues(render_cell(model, delegate, 0, 2))
    check("起跑前是灰色 … (中性, 不蓝不红)",
          neutral > 0 and blue == 0 and red == 0,
          f"blue={blue} red={red} neutral={neutral}")
    model.mark_loading(0, 2)
    blue, red, neutral = count_hues(render_cell(model, delegate, 0, 2))
    check("加载中是蓝色 …", blue > 0 and red == 0, f"blue={blue} red={red}")
    model.mark_failed(0, 2)
    blue, red, neutral = count_hues(render_cell(model, delegate, 0, 2))
    check("失败是红色 ×", red > 0 and blue == 0, f"blue={blue} red={red}")
    # 失败 '×' 必须是 **14pt 粗体** —— Tk 原版是
    # font=('TkDefaultFont', 14, 'bold'), 10pt 常规体不够显眼。
    # 跟两个对照组比: 10pt 常规 (要更高) / 14pt 常规 (要更粗 → 墨更多)。
    fail_img = render_cell(model, delegate, 0, 2)
    fail_box = red_ink_bbox(fail_img)
    ctrl10 = QFont(video_qt.ui_font())
    ctrl14 = QFont(video_qt.ui_font())
    ctrl14.setPointSize(14)
    box10 = red_ink_bbox(render_glyph('×', ctrl10))
    check("失败 '×' 比 10pt 常规体**更高** (字号确实变大了)",
          fail_box[3] > box10[3],
          f"delegate_h={fail_box[3]} 10pt_h={box10[3]}")
    _b, _r, _n = count_hues(render_glyph('×', ctrl14))
    check("失败 '×' 比 14pt **常规**体墨更多 (确实是粗体)",
          red > _r,
          f"delegate_red={red} 14pt_regular_red={_r}")
    fail_font = video_qt.snapshot_fail_font()
    check("失败字形的字体规格 = 14pt 粗体 (与 Tk 原版一致)",
          fail_font.pointSize() == 14 and fail_font.bold(),
          f"got={fail_font.pointSize()}pt bold={fail_font.bold()}")
    # 成功 → 委托把 QPixmap 画出来了 (缩略图缓存放在 delegate 里)
    thumb = QPixmap(640, 360)
    thumb.fill(QColor('red'))
    ok = delegate.set_thumbnail(0, 2, _write_png(mkdtemp(prefix='vdcheck_qt_thumb_'),
                                                  'thumb.png'))
    model.mark_done(0, 2, '/tmp/x.png')
    blue, red, neutral = count_hues(render_cell(model, delegate, 0, 2, (120, 80)))
    check("成功 → 委托把 QPixmap 画出来了", ok and red > 0, f"red={red}")

    # ================================================================
    section("快照网格行高: 缩略图按**原尺寸**显示 (修 bug: 被压成 1/6 大小)")
    # 2026-10-29 用户反馈「定点截图的图片展示太小」的根因: 快照网格
    # **从来没设过行高** —— Qt 默认 30px, _paint_pixmap 把 320x180 的
    # 缩略图等比缩到 53x30 显示。Tk 版每格是个 320x180 的 tk.Label,
    # 行高自然就是 ~196px。
    #
    # 旧断言抓不到这个 bug: render_cell() 用的是**合成**的 80x40 rect,
    # opt.rect 是那张图自己的 rect —— **从不使用真实行高** → 缩略图
    # 永远画得下。下面用**真实 view 的真实 cell 矩形**来渲染。
    dlg_h = SnapshotDialog(win, [make_info()], '/tmp/does/not/exist/snapshot')
    dlg_h.model.set_time_tokens(['00:00:07:24'])
    dlg_h._sync_columns()
    dlg_h.resize(1200, 700)
    dlg_h.show()
    app.processEvents()
    check("快照网格的行高装得下缩略图 (>= 180px)",
          dlg_h.view.rowHeight(0) >= video_qt.SNAPSHOT_MEM_H,
          f"got={dlg_h.view.rowHeight(0)} need>={video_qt.SNAPSHOT_MEM_H}")
    check("行高 = SNAPSHOT_ROW_H (196px)",
          dlg_h.view.rowHeight(0) == video_qt.SNAPSHOT_ROW_H,
          f"got={dlg_h.view.rowHeight(0)} want={video_qt.SNAPSHOT_ROW_H}")
    # 前置: 行高得真的比 Qt 默认的 30px 高得多, 否则上面两条跟「没设行高」
    # 分不开。
    check("前置: 行高比 Qt 默认的 30px 高得多",
          dlg_h.view.rowHeight(0) > 30, f"got={dlg_h.view.rowHeight(0)}")
    check("前置: 缩略图是 320x180 (原图 640x360 缩一半)",
          video_qt.SNAPSHOT_MEM_W == 320 and video_qt.SNAPSHOT_MEM_H == 180,
          f"got={video_qt.SNAPSHOT_MEM_W}x{video_qt.SNAPSHOT_MEM_H}")

    # 塞一张真的缩略图进去 → 用**真实 cell 矩形**渲染 → 像素级验尺寸
    thumb_dir = mkdtemp(prefix='vdcheck_qt_thumb_')
    thumb_path = os.path.join(thumb_dir, 'thumb.png')
    with open(thumb_path, 'wb') as f:
        f.write(fake_png_bytes(640, 360))          # 纯红的 640x360 原图
    ok_h = dlg_h.delegate.set_thumbnail(0, 2, thumb_path)
    dlg_h.model.mark_done(0, 2, thumb_path)
    app.processEvents()
    real_img = render_real_cell(dlg_h.view, dlg_h.delegate, 0, 2)
    real_box = red_ink_bbox(real_img)
    check("缩略图在**真实行高**下按原尺寸绘制 (320x180, 没被压扁)",
          ok_h and abs(real_box[2] - video_qt.SNAPSHOT_MEM_W) <= 2
          and abs(real_box[3] - video_qt.SNAPSHOT_MEM_H) <= 2,
          f"bbox={real_box} want~({video_qt.SNAPSHOT_MEM_W},"
          f"{video_qt.SNAPSHOT_MEM_H})")
    # 端到端: 直接抓**真实视口** —— 用户眼睛看到的那张图。
    # (旧 bug 下这里会是 53x30 —— 只有 1/6 大小。)
    vp_img = dlg_h.view.viewport().grab().toImage()
    vp_box = red_ink_bbox(vp_img)
    check("真实视口里缩略图就是 320x180 (用户看到的大小)",
          abs(vp_box[2] - video_qt.SNAPSHOT_MEM_W) <= 2
          and abs(vp_box[3] - video_qt.SNAPSHOT_MEM_H) <= 2,
          f"bbox={vp_box}")

    # ================================================================
    section("状态从 role 走到画面 (dataChanged → 重绘)")
    # SNAPSHOT_STATE_ROLE 不能是死的: delegate 必须**从 role 取状态**。
    # 把 model.cell_state() 打瞎 —— delegate 若绕过 role 直接问 model,
    # 就画不出蓝色。
    m6 = SnapshotGridModel([make_info()])
    m6.set_time_tokens(['00:00:07:24'])
    m6.mark_loading(0, 2)
    d6 = SnapshotDelegate()
    m6.cell_state = lambda row, col: None
    try:
        blue6, red6, _ = count_hues(render_cell(m6, d6, 0, 2))
    finally:
        del m6.cell_state
    check("delegate 从 **role** 拿状态 (不是直接问 model.cell_state())",
          blue6 > 0 and red6 == 0, f"blue={blue6} red={red6}")

    # 端到端: model 改状态 → dataChanged → 视图重绘 → 画面真的变了
    m7 = SnapshotGridModel([make_info()])
    m7.set_time_tokens(['00:00:07:24'])
    view6 = QTableView()
    view6.setModel(m7)
    view6.setItemDelegate(SnapshotDelegate())
    view6.setShowGrid(False)
    view6.resize(900, 400)
    view6.show()
    app.processEvents()
    blue_before = count_hues(view6.viewport().grab().toImage())[0]
    check("起跑前视口里没有蓝色 (还没加载)", blue_before == 0,
          f"blue={blue_before}")
    m7.mark_loading(0, 2)
    app.processEvents()
    blue_after = count_hues(view6.viewport().grab().toImage())[0]
    check("dataChanged 之后**画面真的重绘了** (蓝色 …)",
          blue_after > 0, f"blue={blue_after}")

    # ================================================================
    section("标题格不达标时整格红字 (与主表格「只红失败列」刻意不同)")
    m2 = SnapshotGridModel([make_info('bad.mp4', '/x/bad.mp4', passing=False,
                                      bitrate=1000.0),
                             make_info('good.mp4', '/x/good.mp4', passing=True,
                                      bitrate=50000.0)])
    m2.set_time_tokens(['00:00:07:24'])

    def fg_at(m, row, col):
        brush = m.data(m.index(row, col), Qt.ForegroundRole)
        return brush.color().name() if brush else None

    check("不达标行的标题格是红色",
          fg_at(m2, 0, 1) == '#dc143c', f"got={fg_at(m2, 0, 1)}")
    check("达标行的标题格是绿色",
          fg_at(m2, 1, 1) == '#228b22', f"got={fg_at(m2, 1, 1)}")
    check("不达标行的**序号格也红** (整行跟着走)",
          fg_at(m2, 0, 0) == '#dc143c', f"got={fg_at(m2, 0, 0)}")
    # 对照: 主表格的序号列**永不染色** (§6 #15) —— 两边刻意不同
    win3 = video_qt.MainWindow()
    win3._add_result(make_info('bad.mp4', '/x/bad.mp4', passing=False,
                               bitrate=1000.0))
    main_idx = win3.model.data(win3.model.index(0, 0), Qt.ForegroundRole)
    check("对照: 主表格的序号列**不染色** (§6 #15)",
          main_idx is None,
          f"got={main_idx.color().name() if main_idx else None}")

    # ================================================================
    section("对齐: 序号列居中 / 标题列左对齐 (§6 #14)")
    m5 = SnapshotGridModel([make_info()])
    m5.set_time_tokens(['00:00:07:24'])
    check("序号列居中",
          m5.data(m5.index(0, 0), Qt.TextAlignmentRole) == int(Qt.AlignCenter),
          f"got={m5.data(m5.index(0, 0), Qt.TextAlignmentRole)}")
    check("标题列左对齐 + 垂直居中",
          m5.data(m5.index(0, 1), Qt.TextAlignmentRole)
          == int(Qt.AlignLeft | Qt.AlignVCenter),
          f"got={m5.data(m5.index(0, 1), Qt.TextAlignmentRole)}")
    win_align = video_qt.MainWindow()
    win_align._add_result(make_info())
    main_align = win_align.model.data(win_align.model.index(0, 0),
                                      Qt.TextAlignmentRole)
    check("对照: 主表格的序号列也居中 (两边一致)",
          main_align == int(Qt.AlignCenter), f"got={main_align}")

    # ================================================================
    section("标题格按 300px 截断 + ToolTipRole 给完整路径")
    long_title = 'deep/' + 'x' * 120 + '/movie.mp4'
    m3 = SnapshotGridModel([make_info(long_title, '/x/' + long_title)])
    m3.set_time_tokens(['00:00:07:24'])
    shown = m3.data(m3.index(0, 1), Qt.DisplayRole)
    tip = m3.data(m3.index(0, 1), Qt.ToolTipRole)
    check("标题格被截断了 (以三个 ASCII 点结尾)", shown.endswith('...'),
          f"got={shown!r}")
    check("截断后宽度不超标题列宽 (300px)",
          QFontMetrics(video_qt.ui_font()).horizontalAdvance(shown)
          <= video_qt.SNAPSHOT_TITLE_W,
          f"got={shown!r}")
    check("ToolTipRole 给的是**完整路径**",
          tip == video_qt.format_title(m3._files[0]), f"got={tip!r}")
    m4 = SnapshotGridModel([make_info('a.mp4', '/x/a.mp4')])
    m4.set_time_tokens(['00:00:07:24'])
    check("没被截断 → 不给 tooltip",
          m4.data(m4.index(0, 1), Qt.ToolTipRole) is None,
          f"got={m4.data(m4.index(0, 1), Qt.ToolTipRole)!r}")

    # ================================================================
    section("开始截图: 输出文件名 + 只留 320px 缩略图")
    out_root = mkdtemp(prefix='vdcheck_qt_snapout_')
    out_dir = os.path.join(out_root, 'snapshot')
    win4 = video_qt.MainWindow()
    win4.snapshot_dest = out_dir
    files = [make_info('a.mp4', '/x/a.mp4')]
    dlg3 = SnapshotDialog(win4, files, out_dir)

    ensure_calls: list = []
    real_ensure = video_qt.MainWindow.ensure_snapshot_dir

    def spy_ensure(self, path=None):
        ensure_calls.append(path)
        return real_ensure(self, path)

    video_qt.MainWindow.ensure_snapshot_dir = spy_ensure
    try:
        dlg3._on_action_btn()
        finished = pump_until(lambda: dlg3._state != 'running', timeout=20)
    finally:
        video_qt.MainWindow.ensure_snapshot_dir = real_ensure

    check("抽帧跑完了", finished, f"state={dlg3._state}")
    check("「开始截图」调的是 ensure_snapshot_dir (不是自己 os.makedirs)",
          ensure_calls == [out_dir], f"got={ensure_calls}")
    check("截图目录建出来了", os.path.isdir(out_dir), f"path={out_dir!r}")
    expected_name = 'a_t1_00-00-07-24.png'
    check("输出文件名 {base}_t{c+1}_{00-00-07-24}.png",
          os.path.exists(os.path.join(out_dir, expected_name)),
          f"got={sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else None}")
    check("状态行: 抽帧完成: 2/2 (2 成功, 0 失败)",
          dlg3.status_label.text() == '抽帧完成: 2/2 (2 成功, 0 失败)',
          f"got={dlg3.status_label.text()!r}")
    check("抽帧完按钮回到「开始截图」",
          dlg3.action_btn.text() == '开始截图',
          f"got={dlg3.action_btn.text()!r}")
    state = dlg3.model.cell_state(0, 2)
    check("成功的 cell 状态是 done 且带磁盘路径",
          state is not None and state.status == 'done'
          and state.image_path is not None,
          f"got={state}")
    thumb = dlg3.delegate.thumbnail(0, 2)
    check("内存里只留 **320px** 的缩略图",
          thumb is not None and thumb.width() == 320,
          f"got={thumb.width() if thumb else None}")
    disk = QImage(state.image_path)
    check("磁盘上存的是 **640px** 原图", disk.width() == 640,
          f"got={disk.width()}")
    big = EnlargeDialog(dlg3, image_path=state.image_path, title='a.mp4 @ T1')
    check("放大窗口从磁盘重新加载 → 640px (不复用内存里那张 320px)",
          big._pixmap.width() == 640, f"got={big._pixmap.width()}")
    check("放大窗口**内存里只留一份 640px 图** (没有 QImage + QPixmap 两份)",
          _img_attrs(big) == ['_pixmap'],
          f"got={_img_attrs(big)}")

    # ================================================================
    section("失败计数端到端: extract_frame 返回 False")
    # 上面那条端到端跑的是 0 失败 —— `_failed_count` 与状态行里的
    # 「(成功, 失败)」根本没被走到。这里补一个**真的失败**的用例。
    fail_root = mkdtemp(prefix='vdcheck_qt_snapfail_')
    fail_dir = os.path.join(fail_root, 'snapshot')
    dlg_fail = SnapshotDialog(win4, files, fail_dir)

    def fake_extract_fail(input_path, seconds, output_path):
        """第二个时间点抽不出来 (返回 False), 第一个正常。"""
        if '_t2_' in os.path.basename(output_path):
            return False
        with open(output_path, 'wb') as f:
            f.write(fake_png_bytes())
        return True

    saved_extract = video_qt.extract_frame
    video_qt.extract_frame = fake_extract_fail
    try:
        dlg_fail._on_action_btn()
        finished = pump_until(lambda: dlg_fail._state != 'running', timeout=20)
    finally:
        video_qt.extract_frame = saved_extract
    check("有失败时也跑完了", finished, f"state={dlg_fail._state}")
    check("_failed_count == 1", dlg_fail._failed_count == 1,
          f"got={dlg_fail._failed_count}")
    check("状态行: 抽帧完成: 2/2 (1 成功, 1 失败)",
          dlg_fail.status_label.text() == '抽帧完成: 2/2 (1 成功, 1 失败)',
          f"got={dlg_fail.status_label.text()!r}")
    check("失败的 cell 状态是 failed",
          dlg_fail.model.cell_state(0, 3) is not None
          and dlg_fail.model.cell_state(0, 3).status == 'failed',
          f"got={dlg_fail.model.cell_state(0, 3)}")
    check("成功的 cell 状态是 done",
          dlg_fail.model.cell_state(0, 2) is not None
          and dlg_fail.model.cell_state(0, 2).status == 'done',
          f"got={dlg_fail.model.cell_state(0, 2)}")

    # ================================================================
    section("点缩略图 → 放大窗口 (标题 {文件名} @ T{c+1})")
    created: list = []

    class FakeEnlarge:
        def __init__(self, parent, image_path, title):
            created.append((image_path, title))

        def show(self):
            pass

    video_qt.EnlargeDialog = FakeEnlarge
    try:
        dlg3._on_cell_clicked(dlg3.model.index(0, 2))
    finally:
        video_qt.EnlargeDialog = real_enlarge
    check("点缩略图 → 弹放大窗口 (路径 + 标题)",
          created and created[0][0] == state.image_path
          and created[0][1] == 'a.mp4 @ T1',
          f"got={created}")

    # ================================================================
    section("抽帧中点关闭 → 二次确认「抽帧还在进行中, 确定要关闭吗?」")
    dlg4 = SnapshotDialog(win4, files, out_dir)
    dlg4._state = 'running'
    msgbox_log.clear()
    msgbox_answer['question'] = QMessageBox.No
    dlg4.close()
    question_texts = [t for kind, _title, t in msgbox_log if kind == 'question']
    check("二次确认的文案**逐字**一致 (半角逗号 + 空格 + 半角问号)",
          question_texts == ['抽帧还在进行中, 确定要关闭吗?'],
          f"got={question_texts}")
    check("回答「否」→ 不关窗, 也不取消抽帧",
          not dlg4._closed and not dlg4._cancel.is_set(),
          f"closed={dlg4._closed} cancel={dlg4._cancel.is_set()}")
    msgbox_answer['question'] = QMessageBox.Yes
    dlg4.close()
    check("回答「是」→ 取消抽帧并关窗",
          dlg4._closed and dlg4._cancel.is_set(),
          f"closed={dlg4._closed} cancel={dlg4._cancel.is_set()}")
    msgbox_answer['question'] = QMessageBox.No

    # ================================================================
    section("Esc 键也走二次确认 (不能绕过 closeEvent)")
    # QDialog 的 Esc 走 reject() → done() → hide(), **不经过 closeEvent** ——
    # 只覆写 closeEvent 的话 Esc 会凭空把弹窗关掉, 不确认也不取消抽帧。
    dlg_esc = SnapshotDialog(win4, files, out_dir)
    dlg_esc._state = 'running'
    dlg_esc.show()
    app.processEvents()
    msgbox_log.clear()
    msgbox_answer['question'] = QMessageBox.No
    esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    QApplication.sendEvent(dlg_esc, esc)
    question_texts = [t for kind, _title, t in msgbox_log if kind == 'question']
    check("Esc 键也弹二次确认 (逐字一致)",
          question_texts == ['抽帧还在进行中, 确定要关闭吗?'],
          f"got={question_texts}")
    check("Esc 回答「否」→ 不关窗, 也不取消抽帧",
          not dlg_esc._closed and not dlg_esc._cancel.is_set()
          and dlg_esc.isVisible(),
          f"closed={dlg_esc._closed} cancel={dlg_esc._cancel.is_set()} "
          f"visible={dlg_esc.isVisible()}")
    msgbox_answer['question'] = QMessageBox.Yes
    QApplication.sendEvent(dlg_esc, esc)
    check("Esc 回答「是」→ 取消抽帧并关窗",
          dlg_esc._closed and dlg_esc._cancel.is_set()
          and not dlg_esc.isVisible(),
          f"closed={dlg_esc._closed} cancel={dlg_esc._cancel.is_set()} "
          f"visible={dlg_esc.isVisible()}")
    msgbox_answer['question'] = QMessageBox.No

    # ---- 窗口 X 与 Esc 共用同一个确认框, 不能弹两次 ----
    # **必须先 show()**: 窗口 X 那条路上 QDialog::closeEvent 内部会再调一次
    # reject() —— 只有对话框**可见**时 Qt 才会走那一步 (隐藏时 close() 只发
    # QCloseEvent, 不调 reject())。生产里弹窗是 exec() 打开的, 一直可见。
    dlg_x = SnapshotDialog(win4, files, out_dir)
    dlg_x._state = 'running'
    dlg_x.show()
    app.processEvents()
    msgbox_log.clear()
    msgbox_answer['question'] = QMessageBox.Yes
    dlg_x.close()
    n_questions = len([1 for kind, _t, _x in msgbox_log if kind == 'question'])
    check("窗口 X 只弹**一次**二次确认 (closeEvent → reject 不重复弹)",
          n_questions == 1, f"got={n_questions}")
    check("窗口 X 回答「是」→ 取消抽帧并关窗",
          dlg_x._closed and dlg_x._cancel.is_set(),
          f"closed={dlg_x._closed} cancel={dlg_x._cancel.is_set()}")
    msgbox_answer['question'] = QMessageBox.No

    # ================================================================
    section("时间点输入框: 双击只选所在的那一段")
    edit = TimeTokenEdit('00:00:07:24')
    edit.setFixedWidth(200)
    edit.show()
    app.processEvents()
    for idx, want, label in ((0, '00', 'HH'), (3, '00', 'MM'),
                             (6, '07', 'SS'), (9, '24', 'frame')):
        x = x_for_char(edit, idx)
        _double_click(edit, x)
        check(f"双击第 {idx} 个字符 → 只选 {label} 段 ({want!r})",
              edit.selectedText() == want, f"got={edit.selectedText()!r}")

    # ================================================================
    section("横向可滚: 5 个时间点时列总宽超出视口")
    dlg5 = SnapshotDialog(win4, files, out_dir)
    dlg5.model.set_time_tokens(['00:00:01:00'] * 5)
    dlg5._sync_columns()
    dlg5.resize(1200, 700)
    dlg5.show()
    app.processEvents()
    hsb = dlg5.view.horizontalScrollBar()
    total_w = (video_qt.SNAPSHOT_IDX_W + video_qt.SNAPSHOT_TITLE_W
               + 5 * video_qt.SNAPSHOT_CELL_W)
    check("5 个时间点时列总宽超出视口",
          total_w > dlg5.view.viewport().width(),
          f"total={total_w} viewport={dlg5.view.viewport().width()}")
    check("横向滚动条有范围 (可以滚)", hsb.maximum() > 0, f"max={hsb.maximum()}")
    hsb.setValue(hsb.maximum())
    app.processEvents()
    check("能横向滚到最右", hsb.value() == hsb.maximum(),
          f"value={hsb.value()} max={hsb.maximum()}")
    check("滚到最右时最后一列露出来了",
          dlg5.view.columnViewportPosition(6)
          + dlg5.view.columnWidth(6) <= dlg5.view.viewport().width() + 1,
          f"pos={dlg5.view.columnViewportPosition(6)}")

    # ================================================================
    section("表头与数据列对齐 (表头文字来自模型)")
    header = dlg5.view.horizontalHeader()
    check("表头的 model 就是网格 model", header.model() is dlg5.model)
    check("表头文字来自 model.headerData()",
          header.model().headerData(2, Qt.Horizontal) == 'T1: 00-00-01-00',
          f"got={header.model().headerData(2, Qt.Horizontal)!r}")
    check("表头列数 == 模型列数",
          header.count() == dlg5.model.columnCount(),
          f"header={header.count()} model={dlg5.model.columnCount()}")
    # (2026-10-27 删: 「每个表头 section 的文字都与 model.headerData() 一致」
    #  —— 上面那条已经确立了 header.model() is dlg5.model, 那条断言于是
    #  退化成 x == x, **恒真**。这一小节真正有牙的是下面两条: 表头文字的
    #  字面量与列宽。)
    # 旧断言比的是 header.sectionViewportPosition(c) vs
    # view.columnViewportPosition(c) —— 后者**就是**前者的定义 (Qt 里
    # QTableView::columnViewportPosition 直接转调 horizontalHeader()),
    # 差值按构造恒为 0, 永远不可能失败。改成**能被弄坏**的不变式:
    # 列宽是应用自己定的 (45 / 300 / 328), 弄坏了就会红。
    widths = [dlg5.view.columnWidth(c)
              for c in range(dlg5.model.columnCount())]
    check("列宽: 序号 45 / 标题 300 / 每个时间点 328",
          dlg5.view.columnWidth(0) == video_qt.SNAPSHOT_IDX_W
          and dlg5.view.columnWidth(1) == video_qt.SNAPSHOT_TITLE_W
          and all(dlg5.view.columnWidth(c + 2) == video_qt.SNAPSHOT_CELL_W
                  for c in range(len(dlg5.model.time_tokens))),
          f"got={widths}")

    # 注: 旧 Tk 版把表头做成 _inner 的子 widget, 纵向滚动时**跟着内容滚走**
    # (不钉住)。Qt 的表头本来就是独立控件, 天然钉在顶部 —— 这个差异是
    # 「构造方式不同」带来的, 不作为行为保留 (产品文档 §6 没这条)。

    # ================================================================
    section("空文件列表不崩")
    dlg6 = SnapshotDialog(win4, [], out_dir)
    check("0 个文件 → 模型 0 行", dlg6.model.rowCount() == 0,
          f"got={dlg6.model.rowCount()}")
    dlg6._on_action_btn()
    finished = pump_until(lambda: dlg6._state != 'running', timeout=10)
    check("0 个文件也能跑完 (不崩)", finished, f"state={dlg6._state}")

    # ================================================================
    section("建目录只有两个口子 (整个模块扫)")
    owners = module_makedirs_owners()
    check("只有 ensure_snapshot_dir 与 _move_files 会建目录",
          sorted(f for _cls, f in owners) == ['_move_files', 'ensure_snapshot_dir'],
          f"got={sorted(owners)}")

    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


def _write_png(directory: str, name: str) -> str:
    path = os.path.join(directory, name)
    with open(path, 'wb') as f:
        f.write(fake_png_bytes())
    return path


if __name__ == '__main__':
    sys.exit(main())
