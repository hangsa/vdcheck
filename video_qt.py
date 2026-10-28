"""PySide6 版的界面层。

表格用 QAbstractTableModel + QTableView: 着色走 Qt.ForegroundRole,
tooltip 走 Qt.ToolTipRole, 虚拟滚动/列宽拖拽/横向滚动全部用 Qt 原生能力,
表格区**一个原生子窗口都没有** —— 这是「最小化恢复时表格区黑一片」的根治点
(Qt 顶层窗口是离屏渲染 + 一次性 blit, 不存在画了一半的中间态)。
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
from dataclasses import dataclass

from PySide6.QtCore import (
    QAbstractTableModel, QEvent, QModelIndex, QObject, Qt, QThread, Signal, Slot,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QKeySequence, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QStatusBar, QStyledItemDelegate, QTableView, QVBoxLayout, QWidget,
)

from video_core import (
    VIDEO_EXTENSIONS, VideoInfo, _cell_fg, _extract_sort_key,
    _header_text_with_arrow, _next_sort_state, format_title, parse_threshold,
    parse_video_info, run_ffprobe, scan_video_files, truncate_text,
)

# 阈值默认值。这两个常量现在住在 video_checker.py (Tk 版); Qt 层自带一份,
# 免得反过来依赖 Tk 入口 —— **Task 7 删 Tk 时并回 video_core.py**
# (否则同一个字面量会有两份, 改一处忘一处)。
DEFAULT_BITRATE_KBPS = 30000
DEFAULT_SAMPLE_RATE_KHZ = 48

_ui_font: QFont | None = None


def ui_font() -> QFont:
    """全 app 统一的字体 (9pt)。**必须在 QApplication 建好之后求值**。

    QFont() 会拷贝**当前 app 的**默认字体; app 还不存在时字体族是空的
    (family=''), 于是字宽度量整套偏掉 ('码率(kbps)' 48px → 67px),
    QFontMetrics(那个字体) 甚至直接 abort 进程:
        QFontDatabase: Must construct a QGuiApplication before
        accessing QFontDatabase

    所以这里是**惰性**的 —— 第一次有人要用字体时才建, 而不是 import 时。

    app 还不存在时**明确报错**, 而不是给一个用不了的字体:
    QFontMetrics 没有 QGuiApplication 就 abort (实测退出码 134), 换任何
    字体族都一样 (三种构造方式全 134) —— 与其让人在别处撞上那个 abort,
    不如在这里说清楚。
    """
    global _ui_font
    if _ui_font is None:
        if QApplication.instance() is None:
            raise RuntimeError(
                'ui_font() 必须在 QApplication 建好之后调用: '
                'QFontMetrics 没有 QGuiApplication 会直接 abort 进程 '
                '(退出码 134), 字体族是不是空的不影响这件事')
        font = QFont('Segoe UI', 9) if sys.platform == 'win32' else QFont()
        if sys.platform != 'win32':
            font.setPointSize(9)
        _ui_font = font
        return font
    return _ui_font


def __getattr__(name: str):
    """模块级惰性属性 (PEP 562)。

    只为 UI_FONT 一个名字存在: `video_qt.UI_FONT` **属性访问**每次都拿到
    **当前 app 的**字体, 而不是 import 时刻那个字体族为空的空壳。

    注意: `from video_qt import UI_FONT` 走的是**快照**语义 —— 绑定那一刻
    求值一次, 之后 app 建好了也不会刷新。代码里**一律用 ui_font()**,
    不要用 from-import 拿 UI_FONT。

    PEP 562: 这里只能抛 AttributeError —— 抛别的异常的话,
    hasattr() / getattr(..., default) 对这一个名字就失灵了。
    """
    if name == 'UI_FONT':
        try:
            return ui_font()
        except RuntimeError as e:
            raise AttributeError(str(e)) from e
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def apply_app_font(app: QApplication) -> None:
    """全 app 统一 9pt 字体。

    模型用 QFontMetrics(ui_font()) 量宽度, 视图用 app 的字体渲染 ——
    两者不一致的话, 预截断好的文字会**溢出列宽** (Qt 的 elide 是关的)。
    """
    app.setFont(ui_font())


# 单元格对齐只有两种 (产品文档 §4.2): title 列左对齐, 其余全部居中。
# QTableView 的默认对齐是左对齐, 不显式给 TextAlignmentRole 就会全表左对齐
# —— 与现状 (Tk 里非 title 列居中) 不一致。
_ALIGN_FLAGS = {
    'w': int(Qt.AlignLeft | Qt.AlignVCenter),
    'center': int(Qt.AlignCenter),
}


@dataclass(frozen=True)
class ColumnSpec:
    """一列的规格。key=None 表示序号列。"""
    key: str | None
    header: str
    width: int
    align: str = 'center'      # 'w' | 'center'
    sortable: bool = False


VIDEO_COLUMNS: list[ColumnSpec] = [
    ColumnSpec(None, '序号', 45, 'center', False),
    ColumnSpec('title', '标题', 250, 'w', False),
    ColumnSpec('resolution', '分辨率', 90),
    ColumnSpec('frame_rate', '帧率(fps)', 75, 'center', True),
    ColumnSpec('bitrate', '码率(kbps)', 85, 'center', True),
    ColumnSpec('spec', '规格', 140),
    ColumnSpec('audio_channels', '声道数', 55),
    ColumnSpec('audio_sample_rate', '采样率(kHz)', 90, 'center', True),
    ColumnSpec('duration', '时长', 75),
    ColumnSpec('file_size', '文件大小', 75),
    ColumnSpec('result', '结果', 60, 'center', True),
]


class VideoTableModel(QAbstractTableModel):
    """主表格的数据模型。

    颜色规则、排序键、箭头文字全部**复用 video_core 的纯逻辑**,
    这里只负责把它们翻译成 Qt 的 role。
    """

    def __init__(self, columns: list[ColumnSpec], parent=None):
        super().__init__(parent)
        self._columns = columns
        self._rows: list[VideoInfo] = []
        self._widths: dict[int, int] = {i: c.width for i, c in enumerate(columns)}
        self.sort_key: str | None = None
        self.sort_desc: bool = False
        self._fm = QFontMetrics(ui_font())

    # ---- Qt 必需接口 ----

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        if not 0 <= row < len(self._rows):
            return None                    # 过期 index (clear() 之前取的)
        section = index.column()
        if not 0 <= section < len(self._columns):
            return None
        col = self._columns[section]
        info = self._rows[row]
        if role == Qt.DisplayRole:
            if col.key is None:                    # 序号列: 排序后的新位置
                return str(row + 1)
            return self._truncated(section, info)
        if role == Qt.ForegroundRole:
            fg = _cell_fg(info, col.key)
            if fg is None:
                # 该格**不着色** → 返回 None 让 Qt 用调色板默认色。
                # 写死黑色的话深色主题下就是黑字黑底 (规格 §3.3 写的是
                # 「其余保持默认色」)。序号列也一样 —— 它是「永不染色」,
                # 不是「永远是黑色」。
                return None
            return QBrush(QColor(fg))
        if role == Qt.TextAlignmentRole:
            return _ALIGN_FLAGS.get(col.align, int(Qt.AlignCenter))
        if role == Qt.ToolTipRole:
            if col.key is None:
                return None
            full = self._full_text(section, info)
            return full if full != self._truncated(section, info) else None
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation != Qt.Horizontal:
            return None
        if section < 0 or section >= len(self._columns):
            return None
        col = self._columns[section]
        if role == Qt.TextAlignmentRole:
            # 表头按 section 对齐: title 列左对齐, 其余居中 (产品文档 §4.2)。
            # QHeaderView 没有「按 section 设对齐」的 API, 但它**读模型给的
            # TextAlignmentRole** —— 实测 (离屏渲染取像素) 表头文字确实跟着
            # 这个 role 走, 所以不用自己覆写 paintSection 画一遍。
            return _ALIGN_FLAGS.get(col.align, int(Qt.AlignCenter))
        if role != Qt.DisplayRole:
            return None
        base = col.header
        if col.key is not None and col.sortable and self.sort_key == col.key:
            base = _header_text_with_arrow(base, True, self.sort_desc)
        # 表头文字也按列宽截断 (加完箭头再截, 与现状 _refresh_headers 同序)
        return truncate_text(base, self._widths[section], self._measure)

    # ---- 内部 ----

    def _measure(self, text: str) -> int:
        return self._fm.horizontalAdvance(text)

    def _full_text(self, section: int, info: VideoInfo) -> str:
        key = self._columns[section].key
        if key == 'title':
            return format_title(info)
        if key == 'spec':
            return (f"{info.container_format}/{info.video_codec}"
                    f"/{info.audio_codec}")
        if key == 'result':
            return '达标' if info.is_passing else '不达标'
        return {
            'resolution': info.resolution,
            'frame_rate': info.frame_rate,
            'bitrate': f"{info.bitrate_kbps:.0f}",
            'audio_channels': info.audio_channels,
            'audio_sample_rate': info.audio_sample_rate,
            'duration': info.duration,
            'file_size': info.file_size,
        }[key]

    def _truncated(self, section: int, info: VideoInfo) -> str:
        return truncate_text(self._full_text(section, info),
                             self._widths[section], self._measure)

    def append_result(self, info: VideoInfo) -> None:
        r = len(self._rows)
        self.beginInsertRows(QModelIndex(), r, r)
        self._rows.append(info)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()

    @property
    def sorted_rows(self) -> list[VideoInfo]:
        """当前显示序的行 (排序后)。

        app 侧另有一份**插入序**的列表专供「移动文件」用 —— 现状也是
        移动按插入序走, 与表格显示序无关 (见设计文档 §4)。
        """
        return list(self._rows)

    def set_column_width(self, section: int, width: int) -> None:
        """列宽变化 → 重新截断该列的表头文字与单元格文本。"""
        if not 0 <= section < len(self._columns):
            return                              # 越界: 不往 dict 里塞野列
        width = max(20, width)                  # 最小 20px, 与现状一致
        if self._widths.get(section) == width:
            return
        self._widths[section] = width
        # headerDataChanged 的签名是 (orientation, first, last) —— **没有**
        # topLeft (那是 dataChanged 才有的)。PySide6 6.12 的绑定也是三参。
        self.headerDataChanged.emit(Qt.Horizontal, section, section)
        top = self.index(0, section)
        bottom = self.index(max(0, self.rowCount() - 1), section)
        if top.isValid():
            self.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.ToolTipRole])

    def on_header_clicked(self, section: int) -> None:
        """表头点击 → 三态循环 → 就地排序。"""
        if not 0 <= section < len(self._columns):
            return                              # 越界: 无反应
        col = self._columns[section]
        if col.key is None or not col.sortable:
            return                              # 不可排序列: 完全无反应
        self.sort_key, self.sort_desc = _next_sort_state(
            self.sort_key, self.sort_desc, col.key)
        self._apply_sort()
        # 箭头变了 → 所有表头文字都要重写。取消排序那一档 _apply_sort 早退
        # (不发 layoutChanged), 不显式通知的话表头上会留着一个过期箭头
        # (Tk 版每次点击都显式重写所有表头 label, 见 _refresh_headers)。
        self.headerDataChanged.emit(Qt.Horizontal, 0, len(self._columns) - 1)

    def is_sorted(self) -> bool:
        """是否处于排序状态 (供 App 在扫描完成后决定是否 reorder)。"""
        return self.sort_key is not None

    def reorder(self) -> None:
        """按当前排序状态重排现有行。无排序状态或 0 行时为 no-op。

        扫描过程中结果按插入序追加; 扫描结束后 App 调它统一排一次 ——
        与 Tk 版 VideoGridView.reorder() 同名同义。
        """
        self._apply_sort()

    def _apply_sort(self) -> None:
        if self.sort_key is None or not self._rows:
            return
        old_rows = list(self._rows)
        self.layoutAboutToBeChanged.emit()
        # list.sort 稳定, 同级保留插入序
        self._rows.sort(key=lambda i: _extract_sort_key(i, self.sort_key),
                        reverse=self.sort_desc)
        # 视图的 current / selection 是按**行号**存的, 排序后行号变了 →
        # 选中的就不是同一个文件了。用 persistent index 把「同一个文件」
        # 在排序前后的行号对应起来通知视图 (Qt 排序模型的标准做法)。
        new_pos = {id(info): i for i, info in enumerate(self._rows)}
        for idx in self.persistentIndexList():
            if not 0 <= idx.row() < len(old_rows):
                continue
            new_row = new_pos.get(id(old_rows[idx.row()]))
            if new_row is None or new_row == idx.row():
                continue
            self.changePersistentIndex(
                idx, self.createIndex(new_row, idx.column()))
        self.layoutChanged.emit()


class TruncatingDelegate(QStyledItemDelegate):
    """表格 delegate。

    文本在 model 的 data() 里**已经截断好**, 这里关掉 Qt 自己的 elide
    (默认是单字符 '…', 与现状的三个 ASCII 点 '...' 不一致)。
    """
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.textElideMode = Qt.ElideNone


# ---- 派生路径的两个小助手 ----
#
# 「浏览」选的是**父目录**, 下面套一层固定子目录 (checked / snapshot),
# 与默认值 (<文件路径>\checked / <文件路径>\snapshot) 同一套约定。
# 斜杠约定是**刻意的不对称** (产品文档 §6 #3): 迁移地址无条件转反斜杠,
# 截图地址只在 win32 转 —— macOS 上反斜杠路径 os.path.isdir / makedirs 不认,
# 建出来的目录会等于没建。


def checked_dest_for(parent: str) -> str:
    """迁移地址 = 父目录 + 固定子目录 checked。**无条件**转反斜杠。"""
    return os.path.join(parent, 'checked').replace('/', '\\')


def snapshot_dest_for(parent: str) -> str:
    """截图地址 = 父目录 + 固定子目录 snapshot。**只在 win32** 转反斜杠。"""
    path = os.path.join(parent, 'snapshot')
    return path.replace('/', '\\') if sys.platform == 'win32' else path


class ScanStats:
    """一轮扫描过程中的共享计数。

    worker 线程写、主线程读 —— 不走信号: 为了一个整数再发一个信号不值当,
    而且 worker 那个线程比主线程先结束, 信号回传还得管它的生命周期。
    """

    def __init__(self) -> None:
        self.total_files = 0


# 关窗口时没等完的扫描线程。绝不能析构一个还在跑的 QThread (会 abort),
# 所以摘出 parent 关系存在这里, 由 main() 在退出前等它自然结束。
_ORPHANED_THREADS: list[QThread] = []


class ScanWorker(QObject):
    """扫描工作线程的主体。

    住在 QThread 里, 用四个信号把进度与结果送回主线程:
      progress(str)         状态栏那一行字
      result_ready(VideoInfo)  一个文件的结果 (边扫边出)
      finished()            正常扫完
      cancelled()           被用户取消
    """

    result_ready = Signal(object)
    progress = Signal(str)
    finished = Signal()
    cancelled = Signal()

    def __init__(self, kind: str, payload,
                 bitrate_std: float, sample_rate_std: float,
                 cancel_event: threading.Event, stats: ScanStats,
                 parent=None):
        super().__init__(parent)
        self._kind = kind                 # 'dir' | 'files'
        self._payload = payload           # ('dir', (目录, 递归)) 或 ('files', [路径])
        self._bitrate_std = bitrate_std
        self._sample_rate_std = sample_rate_std
        self._cancel = cancel_event
        self._stats = stats

    @Slot()
    def run(self) -> None:
        if self._kind == 'dir':
            self.progress.emit('正在扫描文件列表...')
            directory, recursive = self._payload
            files = scan_video_files(directory, recursive)
            base_path = directory
        else:
            files = list(self._payload)
            # 拖进来的文件: 相对路径锚在**第一个文件**所在目录
            base_path = os.path.dirname(files[0]) if files else '.'
        total = len(files)
        self._stats.total_files = total
        if self._kind == 'files':
            self.progress.emit(f'正在检测 {total} 个文件...')
        if total == 0:
            if self._kind == 'dir':
                self.progress.emit('未找到视频文件')
            self.finished.emit()
            return

        for i, fp in enumerate(files, 1):
            if self._cancel.is_set():
                break
            self.progress.emit(
                f'正在检测... ({i}/{total}) {os.path.basename(fp)}')
            data = run_ffprobe(fp)
            if data is None:
                continue                    # ffprobe 失败/超时 → 静默跳过
            info = parse_video_info(data, fp, base_path,
                                    self._bitrate_std, self._sample_rate_std)
            if info is not None:
                self.result_ready.emit(info)

        if self._cancel.is_set():
            self.cancelled.emit()
        else:
            self.finished.emit()


class MainWindow(QMainWindow):
    """主窗口。

    布局照产品文档 §4.1 逐控件搭; 文案照 §4.3 逐字抄 (含全角冒号、间隔号,
    以及「正在检测中，请等待完成后再清除。」(全角逗号) vs
    「正在检测中, 请等待完成后再截图。」(半角逗号) 这种**故意的不一致**)。
    """

    status_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('视频检测v3.3')
        self.resize(1280, 700)
        self.setMinimumSize(1000, 500)

        # 全 app 统一 9pt —— **在窗口这一层就 apply**, 不只 main() 里做:
        # 任何人直接 `MainWindow()` (断言脚本、将来的嵌入用法) 拿到的都
        # 必须是同一套字体, 否则模型按 9pt 量宽、视图按 app 默认字体渲染,
        # 预截断好的文字会溢出列宽 (清单 #2)。
        app = QApplication.instance()
        if app is not None:
            apply_app_font(app)
            # app 退出 (app.quit()) 时也要把扫描线程停掉 —— 不然 QThread
            # 会在运行中被析构, 进程直接 abort。
            app.aboutToQuit.connect(self._stop_scan)

        # ---- 数据 ----
        # video_results 是**插入序**的那份 —— 「移动文件」按它走,
        # 与表格的显示序 (排序后) 无关 (产品文档 §6 的现状行为)。
        self.video_results: list[VideoInfo] = []
        self.scanning = False
        self._cancel = threading.Event()
        # 拖拽留下的文件列表 (None = 扫路径框里的整个目录)
        self._selected_files: list[str] | None = None
        # 达标数增量维护 (避免每次插入都全量 sum)
        self._passed_count = 0
        self._stats = ScanStats()
        self._thread: QThread | None = None
        self._worker: ScanWorker | None = None

        self._build_widgets()

    # ---- 布局 ----

    def _build_widgets(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(5, 5, 5, 5)
        root.setSpacing(4)

        # === Row 1: 路径输入行 ===
        row1 = QHBoxLayout()
        row1.addWidget(QLabel('文件路径:'))
        self.path_edit = QLineEdit()
        self.path_edit.setMinimumWidth(200)
        # 用户手动改路径框 → 丢弃「拖拽留下的选文件列表」模式
        self._suppress_path_trace = False
        self.path_edit.textChanged.connect(self._on_path_edited)
        row1.addWidget(self.path_edit, 1)

        browse_btn = QPushButton('浏览')
        browse_btn.clicked.connect(self._browse_path)
        row1.addWidget(browse_btn)

        self.recursive_check = QCheckBox('子文件夹')
        row1.addWidget(self.recursive_check)

        self.scan_btn = QPushButton('开始检测')
        self.scan_btn.clicked.connect(self._on_scan_btn_click)
        row1.addWidget(self.scan_btn)
        root.addLayout(row1)

        # === Row 2: 码率标准 + 采样率标准 ===
        row2 = QHBoxLayout()
        row2.addWidget(QLabel('码率标准:'))
        self.bitrate_edit = QLineEdit(str(DEFAULT_BITRATE_KBPS))
        self.bitrate_edit.setFixedWidth(80)
        row2.addWidget(self.bitrate_edit)
        row2.addWidget(QLabel('kbps'))

        row2.addSpacing(15)
        row2.addWidget(QLabel('采样率标准:'))
        self.sample_rate_edit = QLineEdit(str(DEFAULT_SAMPLE_RATE_KHZ))
        self.sample_rate_edit.setFixedWidth(80)
        row2.addWidget(self.sample_rate_edit)
        row2.addWidget(QLabel('kHz'))

        # 「定点截图」在 kHz 标签右侧 (产品文档 §4.1)
        snapshot_btn = QPushButton('定点截图')
        snapshot_btn.clicked.connect(self._on_snapshot_btn_click)
        row2.addWidget(snapshot_btn)

        # 「清除记录」右对齐, 右侧留 272px —— Tk 版是硬编码 padx=(0, 272)
        # (产品文档 §6 #31, 视觉对齐的历史值), 这里照搬。
        row2.addStretch(1)
        clear_btn = QPushButton('清除记录')
        clear_btn.clicked.connect(self._clear_records)
        row2.addWidget(clear_btn)
        row2.addSpacing(272)
        root.addLayout(row2)

        # === 表格区 ===
        self.model = VideoTableModel(VIDEO_COLUMNS, self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setShowGrid(False)          # Qt 默认画 1px 网格线, Tk 没有
        self.view.setSortingEnabled(False)    # 不用 Qt 的排序指示箭头
        self.view.setDragDropMode(QAbstractItemView.NoDragDrop)
        self.view.setAcceptDrops(False)
        self.view.viewport().setAcceptDrops(False)
        self.view.setItemDelegate(TruncatingDelegate(self.view))
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._setup_header()
        root.addWidget(self.view, 1)

        # === Row 3: 迁移地址 + 移动按钮 ===
        row3 = QHBoxLayout()
        row3.addWidget(QLabel('迁移地址:'))
        self.dest_edit = QLineEdit()
        self.dest_edit.setMinimumWidth(200)
        row3.addWidget(self.dest_edit, 1)

        browse_dest_btn = QPushButton('浏览')
        browse_dest_btn.clicked.connect(self._browse_dest)
        row3.addWidget(browse_dest_btn)

        move_btn = QPushButton('移动达标文件')
        move_btn.clicked.connect(self._move_passing_files)
        row3.addWidget(move_btn)

        move_all_btn = QPushButton('移动全部文件')
        move_all_btn.clicked.connect(self._move_all_files)
        row3.addWidget(move_all_btn)
        root.addLayout(row3)

        # === 状态栏: 一行文字 ===
        # 终态时后面拼上统计 (见 set_status)。Tk 版是 relief='sunken' 的
        # ttk.Frame, Qt 这边用样式表画一条上边线 —— 同样的凹陷观感。
        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet(
            'QStatusBar { border-top: 1px solid #a0a0a0; }')
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage('就绪')

        # 截图地址: 主窗口只存**默认值** (真地址在弹窗里可改),
        # 与现状的 snapshot_dest_var 一样。
        self.snapshot_dest = ''

        # 全局快捷键: 只绑 <Control-o> → 浏览文件夹
        self._shortcut = QShortcut(QKeySequence('Ctrl+O'), self)
        self._shortcut.activated.connect(self._browse_path)

        # 拖拽: 整个表格区 (表头 + 正文) 都要能接住
        self.setAcceptDrops(True)
        for target in (self.view.viewport(), self.view.horizontalHeader()):
            target.installEventFilter(self)

        self._apply_model_widths()

    def _setup_header(self) -> None:
        """表头: 排序点击、列宽两个方向的接线、序号列固定。"""
        header = self.view.horizontalHeader()
        # 模型已经把表头文字截断好了, Qt 再 elide 一次就是双重截断
        header.setTextElideMode(Qt.ElideNone)
        header.setMinimumSectionSize(20)     # 最小 20px, 与现状一致
        header.setSectionsMovable(False)
        header.setStretchLastSection(False)
        # 序号列固定 45px (产品文档 §6 #15), 其余可拖
        for i, col in enumerate(VIDEO_COLUMNS):
            mode = (QHeaderView.Fixed if col.key is None
                    else QHeaderView.Interactive)
            header.setSectionResizeMode(i, mode)
        header.sectionClicked.connect(self._on_header_clicked)
        # 用户拖表头 → 模型的列宽跟着变, 否则单元格还按旧宽度截断
        header.sectionResized.connect(self._on_section_resized)

    def _apply_model_widths(self) -> None:
        """model → view: 把模型里的列宽推给视图。

        忘了这一步, 单元格按模型宽度截断、表头按视图宽度画, 表格就错位了。
        """
        for i in range(len(VIDEO_COLUMNS)):
            self.view.setColumnWidth(i, self.model._widths[i])

    # ---- 状态栏 ----

    def set_status(self, text: str, with_counts: bool = False) -> None:
        """状态栏那一行字。

        终态 (完成/停止/清除) 带上统计, 中间状态 (就绪/扫描中) 只有进度文字
        —— 扫描中那条本来就带 (3/10) 的进度, 再挂一串统计反而看不清。

        统计里的「共 N」指**已出结果**的文件数; 停止时的 N/M 里的 M 是找到的
        文件总数, 两个数来源不同 (产品文档 §6 #1)。
        """
        if with_counts:
            total = len(self.video_results)
            passed = self._passed_count
            text = f"{text}：共 {total} · 达标 {passed} · 不达标 {total - passed}"
        self.status_bar.showMessage(text)
        self.status_changed.emit(text)

    # ---- 弹窗 (抽成方法, 断言脚本好替换) ----

    def _ask_directory(self, title: str) -> str:
        """选文件夹对话框 (取消时返回空串)。"""
        return QFileDialog.getExistingDirectory(self, title)

    def _show_info(self, text: str) -> None:
        QMessageBox.information(self, '提示', text)

    def _show_error(self, text: str) -> None:
        QMessageBox.critical(self, '错误', text)

    def _show_move_result(self, text: str) -> None:
        QMessageBox.information(self, '移动结果', text)

    def _ask_yes_no(self, text: str) -> bool:
        answer = QMessageBox.question(
            self, '确认移动', text, QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes)
        return answer == QMessageBox.Yes

    # ---- 派生路径 ----

    @property
    def total_files(self) -> int:
        """本轮扫描找到的文件总数 (worker 写, 主线程读)。"""
        return self._stats.total_files

    @total_files.setter
    def total_files(self, value: int) -> None:
        self._stats.total_files = value

    def _set_path(self, path: str) -> None:
        """程序自己写「文件路径」框 (浏览 / 拖拽), 不触发「用户改过路径」。"""
        self._suppress_path_trace = True
        try:
            self.path_edit.setText(path)
        finally:
            self._suppress_path_trace = False

    def _on_path_edited(self, *_args) -> None:
        """用户手动改了「文件路径」→ 丢掉拖拽留下的选文件列表。

        拖拽会把文件记进 _selected_files, 之后扫的是那批文件而不是路径框里的
        目录。以前要等用户点「清除记录」才切得回去 —— 现在手改路径框就立即
        生效, 不用先清除记录 (产品文档 §6 #11)。
        """
        if self._suppress_path_trace:
            return
        self._selected_files = None

    def _sync_derived_paths(self, directory: str) -> None:
        """迁移地址 + 截图地址跟随源目录, 无条件覆盖。

        这两个字段是**从「文件路径」派生**的, 不是用户自己配的 —— 源目录
        一变就该重算。旧的写法是「字段为空才填」, 区分不了「从没填过」和
        「上次为别的目录填过」。

        调用点三处, 都要走这里, 别再各自拼一遍:
          - _browse_path  选了文件夹, 立刻给反馈 (还没点开始检测)
          - _start_scan   手动改过「文件路径」后开始检测
          - _on_file_drop 拖进来的文件所在目录

        **只填字段, 不建目录** —— 目录等真要用的时候再建 (产品文档 §6 #10)。
        """
        self.dest_edit.setText(checked_dest_for(directory))
        self.snapshot_dest = snapshot_dest_for(directory)

    def _browse_path(self) -> None:
        """浏览源: 只选文件夹 (选文件只能拖拽, 产品文档 §6 #7)。"""
        folder = self._ask_directory('选择视频文件夹')
        if folder:
            self._selected_files = None
            self._set_path(folder)
            self._sync_derived_paths(folder)

    def _browse_dest(self) -> None:
        """选迁移地址: 所选文件夹当**父目录**, 下面套一层 checked。

        输入框始终显示最终目标目录 (= 所选文件夹\\checked), 与默认值
        <文件路径>\\checked 是同一套约定。目录本身等点移动时才建。
        """
        folder = self._ask_directory('选择迁移地址')
        if folder:
            self.dest_edit.setText(checked_dest_for(folder))

    def _browse_snapshot_dest(self) -> None:
        """选截图地址: 同样是父目录 + 套一层 snapshot。

        主窗口这一侧只存**默认值** (弹窗里有自己的输入框), 但「浏览」的
        语义在两处必须一致, 所以走同一个助手。
        """
        folder = self._ask_directory('选择截图地址')
        if folder:
            self.snapshot_dest = snapshot_dest_for(folder)

    def ensure_snapshot_dir(self) -> str:
        """截图目录**按需创建** —— 真要往里写 PNG 时才建。

        扫描 / 浏览阶段一个目录都不许建 (产品文档 §6 #10)。
        """
        path = self.snapshot_dest.strip()
        if not path:
            raise ValueError('请先设置截图地址。')
        os.makedirs(path, exist_ok=True)
        return path

    # ---- 扫描 ----

    def _on_scan_btn_click(self) -> None:
        """扫描按钮点击: 空闲时启动检测, 检测中触发停止。"""
        if self.scanning:
            self._cancel.set()
            self.scan_btn.setText('正在停止...')
            self.scan_btn.setEnabled(False)
        else:
            self._start_scan()

    def _start_scan(self) -> None:
        directory = self.path_edit.text().strip()
        if not directory or not os.path.isdir(directory):
            self._show_error('请输入有效的文件夹路径。')
            return

        bitrate_std = parse_threshold(self.bitrate_edit.text())
        if bitrate_std is None:
            self._show_error('码率标准必须为正数。')
            return

        sample_rate_std = parse_threshold(self.sample_rate_edit.text())
        if sample_rate_std is None:
            self._show_error('采样率标准必须为正数。')
            return

        # 迁移地址 + 截图地址跟随本次扫描的源目录 (无条件覆盖)
        self._sync_derived_paths(directory)

        kind, payload = self._scan_target(directory)
        self._begin_scan(kind, payload, bitrate_std, sample_rate_std)

    def _scan_target(self, directory: str) -> tuple[str, object]:
        """本次扫描的目标: ('files', 文件列表) 或 ('dir', 目录)。

        拖拽加载的文件优先 —— 但只在用户没手动改过「文件路径」时。
        """
        if self._selected_files:
            return ('files', self._selected_files)
        return ('dir', directory)

    def _begin_scan(self, kind: str, payload, bitrate_std: float,
                    sample_rate_std: float) -> None:
        """真正把扫描跑起来 (点按钮与拖拽共用这一条路径)。"""
        self._cancel.clear()
        self.scanning = True
        self.total_files = 0
        self.scan_btn.setText('检测中')
        self.scan_btn.setEnabled(True)
        self.video_results.clear()
        self.model.clear()
        # 新一轮扫描: 计数从头来 (漏了这一步 _passed_count 会带着上一轮的
        # 达标数累加, 状态栏出现「达标 188 · 不达标 -178」这种数)
        self._passed_count = 0

        self._thread = QThread(self)
        if kind == 'dir':
            worker_payload = (payload, self.recursive_check.isChecked())
        else:
            worker_payload = payload
        self._worker = ScanWorker(kind, worker_payload, bitrate_std,
                                  sample_rate_std, self._cancel, self._stats)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.result_ready.connect(self._on_result)
        self._worker.finished.connect(self._scan_complete)
        self._worker.cancelled.connect(self._scan_complete)
        # 标准 Qt 模式: 扫描结束 → 线程退出 + worker 析构
        self._worker.finished.connect(self._thread.quit)
        self._worker.cancelled.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.cancelled.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    @Slot(str)
    def _on_progress(self, text: str) -> None:
        self.set_status(text)

    @Slot(object)
    def _on_result(self, info: VideoInfo) -> None:
        """主线程收到一个结果 → 表格增量刷新。"""
        self._add_result(info)

    def _add_result(self, info: VideoInfo) -> None:
        """加一条结果: 插入序列表 + 达标计数 + 表格。"""
        self.video_results.append(info)
        if info.is_passing:
            self._passed_count += 1
        self.model.append_result(info)

    @Slot()
    def _scan_complete(self) -> None:
        """扫描完成处理。"""
        cancelled = self._cancel.is_set()
        self.scanning = False
        self.scan_btn.setText('开始检测')
        self.scan_btn.setEnabled(True)

        # 扫描中途按插入序追加结果; 若已激活排序, 扫描结束统一 reorder 一次
        if self.model.is_sorted():
            self.model.reorder()

        completed = len(self.video_results)
        passed = sum(1 for v in self.video_results if v.is_passing)
        self._passed_count = passed
        if cancelled:
            queued = self.total_files
            # 只留一个冒号: 原来这条自带计数, 再拼统计就成两个冒号 + 两次「共」
            self.set_status(f"已停止检测（已完成 {completed}/{queued}）",
                            with_counts=True)
        else:
            # 目录里一个视频都没有时, 说「检测完成：共 0 · 达标 0 · 不达标 0」
            # 是废话 —— 用户要知道的其实是「没找到文件」。
            if self.total_files == 0:
                self.set_status('未找到视频文件')
            else:
                self.set_status('检测完成', with_counts=True)

    @Slot()
    def _on_thread_finished(self) -> None:
        """扫描线程收尾。

        QThread 是窗口的 child —— 光把 Python 引用置 None 不会回收 C++ 侧
        那个对象 (扫 1000 次就攒 1000 个 QThread), 所以显式 deleteLater。

        三种情况:
          - 就是当前这一轮 → 清引用
          - 是被 _stop_scan 孤儿化的那一轮 (self._thread 已经清空) → 也清
          - 是上一轮的 (用户又点了一次「开始检测」, self._thread 已经是新的)
            → **不能**碰当前这一轮的引用
        """
        thread = self.sender()
        if thread is self._thread or self._thread is None:
            self._thread = None
            self._worker = None
        if thread is not None:
            thread.deleteLater()

    def _on_header_clicked(self, section: int) -> None:
        """表头点击 → 三态循环 → 就地排序。

        不可排序列点了完全没反应 (连光标都不变) —— 模型里直接早退。
        """
        self.model.on_header_clicked(section)

    def _on_section_resized(self, section: int, old_size: int, new_size: int) -> None:
        """view → model: 用户拖表头改了列宽 → 模型跟着改。

        少了这条, 模型的 _widths 就陈旧了, 单元格还按旧宽度截断。
        """
        self.model.set_column_width(section, new_size)

    # ---- 清除记录 ----

    def _clear_records(self) -> None:
        """清空检测记录与目标路径 (不影响阈值输入)。"""
        if self.scanning:
            self._show_info('正在检测中，请等待完成后再清除。')
            return
        self.video_results.clear()
        self.model.clear()
        self._passed_count = 0
        self.dest_edit.setText('')
        # 截图地址一起清: 两个字段同源, 只清一个会让「清除记录」后截图地址
        # 还指着一个已经跟当前列表无关的目录
        self.snapshot_dest = ''
        self._selected_files = None
        self.set_status('已清除记录', with_counts=True)

    # ---- 定点截图 (弹窗本身是 Task 6) ----

    def _on_snapshot_btn_click(self) -> None:
        """「定点截图」按钮。

        弹窗是 Task 6 的活儿; 主窗口这一侧只管两件事: 扫描中拒绝, 以及
        没有扫描结果时拒绝 (文案逐字照搬产品文档 §4.3)。
        """
        if self.scanning:
            self._show_info('正在检测中, 请等待完成后再截图。')
            return
        if not self.video_results:
            self._show_info('请先扫描视频。')
            return
        self.open_snapshot_dialog()

    def open_snapshot_dialog(self) -> None:
        """打开定点截图弹窗 (Task 6 实现)。

        截图目录**按需创建** —— 弹窗里点「开始截图」时才建。

        TODO(Task 6): 弹窗的「开始截图」按钮**必须**调
        `self.ensure_snapshot_dir()` 建目录 (不能自己 os.makedirs) ——
        「扫描/浏览阶段绝不建目录」这条规矩由那个口子兜着,
        qt-main 里「截图目录只由 ensure_snapshot_dir() 建」那条断言就是
        给 Task 6 立的规矩 (Task 6 要是绕过这个口子, 那条断言会红)。
        """

    # ---- 移动文件 ----

    def _move_passing_files(self) -> None:
        """移动达标文件到目标目录。"""
        passing = [v for v in self.video_results if v.is_passing]
        if not passing:
            self._show_info('没有达标文件需要移动。')
            return
        dest_dir = self.dest_edit.text().strip()
        if not dest_dir:
            self._show_error('请输入目标文件夹路径。')
            return
        if not self._ask_yes_no(
                f"确定要将 {len(passing)} 个达标文件移动到:\n{dest_dir}\n？"):
            return
        self._move_files(passing, dest_dir)

    def _move_all_files(self) -> None:
        """移动全部文件到目标目录。"""
        files_to_move = self.video_results
        if not files_to_move:
            self._show_info('没有文件需要移动。')
            return
        dest_dir = self.dest_edit.text().strip()
        if not dest_dir:
            self._show_error('请输入目标文件夹路径。')
            return
        if not self._ask_yes_no(
                f"确定要将 {len(files_to_move)} 个文件移动到:\n{dest_dir}\n？"):
            return
        self._move_files(files_to_move, dest_dir)

    def _move_files(self, infos: list[VideoInfo], dest_dir: str) -> None:
        """真搬。目录**点按钮时才创建**。"""
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as e:
            self._show_error(f"无法创建目标目录:\n{e}")
            return

        moved = 0
        failed_list = []
        for info in infos:
            try:
                dest_path = self._get_unique_dest(info.full_path, dest_dir)
                shutil.move(info.full_path, dest_path)
                moved += 1
            except (OSError, shutil.Error) as e:
                failed_list.append(f"{info.rel_path}: {e}")

        msg = f"成功移动 {moved} 个文件。"
        if failed_list:
            msg += (f"\n\n{len(failed_list)} 个文件移动失败:\n"
                    + "\n".join(failed_list[:20]))
        self._show_move_result(msg)

    @staticmethod
    def _get_unique_dest(src_path: str, dest_dir: str) -> str:
        """获取不重复的目标路径。

        重名时链式加后缀 (a_1.mp4 → a_1_1.mp4) —— 审计后接受的现状
        (产品文档 §6 #23 / §8 #5)。
        """
        filename = os.path.basename(src_path)
        dest = os.path.join(dest_dir, filename)
        if not os.path.exists(dest):
            return dest

        name, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(dest):
            dest = os.path.join(dest_dir, f"{name}_{counter}{ext}")
            counter += 1
        return dest

    # ---- 拖拽 ----

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        self._handle_drop(event)
        event.acceptProposedAction()

    def eventFilter(self, obj, event) -> bool:
        """表格区 (表头 + 正文) 的拖放转给主窗口处理。

        其实**不装这个过滤器也接得住** —— Qt 的拖放事件会沿 parent 链上溯,
        表格区里没接住的话会一路冒到 QMainWindow (它 setAcceptDrops(True))。
        装上它是为了: ① 不依赖 Qt 的上溯细节; ② 拖放落在表格上时**一定**
        走本窗口的处理, 不会先被 QTableView 自己截胡 (QTableView 默认
        DragDrop 模式, 虽然没有模型能吃下 mimeData, 但流程上多一层变数)。

        产品文档 §3.6: 接收区域是**整个表格区** (含表头与正文)。
        """
        etype = event.type()
        if etype == QEvent.DragEnter:
            if event.mimeData().hasUrls():
                event.acceptProposedAction()
                return True
            return False
        if etype == QEvent.Drop:
            self._handle_drop(event)
            event.acceptProposedAction()
            return True
        return super().eventFilter(obj, event)

    def _handle_drop(self, event) -> None:
        """拖进来的文件 → 立即开始扫描 (不等用户点「开始检测」)。"""
        urls = event.mimeData().urls()
        files = [u.toLocalFile() for u in urls if u.isLocalFile()]
        self._on_file_drop(files)

    def _on_file_drop(self, files: list[str]) -> None:
        """处理拖拽到窗口的文件。"""
        if self.scanning:
            self._show_info('正在检测中，请等待完成后再添加文件。')
            return
        bitrate_std = parse_threshold(self.bitrate_edit.text())
        if bitrate_std is None:
            self._show_error('码率标准必须为正数。')
            return

        sample_rate_std = parse_threshold(self.sample_rate_edit.text())
        if sample_rate_std is None:
            self._show_error('采样率标准必须为正数。')
            return

        # 只按扩展名过滤: 拖进来的**目录**不生效 (产品文档 §6 #33),
        # 非视频扩展名的文件静默跳过
        video_files = [f for f in files
                       if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS]
        if not video_files:
            self._show_info('没有找到视频文件。')
            return

        # 路径框填第一个被拖入文件所在目录 (不含文件名), 迁移地址 + 截图地址
        # 跟随该目录。用 _set_path: 拖拽是程序自己填路径框, 不能触发
        # 「用户改过路径 → 丢弃文件列表」那条。
        directory = os.path.dirname(video_files[0])
        self._set_path(directory.replace('/', '\\'))
        self._sync_derived_paths(directory)
        # 记下拖入的具体文件 —— 拖拽是唯一的选文件入口
        self._selected_files = video_files

        self._begin_scan('files', video_files, bitrate_std, sample_rate_std)

    # ---- 收尾 ----

    def closeEvent(self, event) -> None:
        """关窗口前把扫描线程停掉。"""
        self._stop_scan()
        super().closeEvent(event)

    def _stop_scan(self, timeout_ms: int = 5000) -> None:
        """停掉扫描线程 (取消 + 等它结束)。

        **不用 QThread.terminate()** —— 目标平台是 Windows, terminate 一个
        持有 GIL 的 Python 线程会把**整个进程**挂死 (ffprobe 子进程还会
        变成孤儿)。取消标志让 worker 在**当前文件**跑完后退出循环;
        ffprobe 自带 30s 超时, 等它自然结束是安全的。

        等不出来 (比如 ffprobe 卡在网络盘上) 就不等了, 但**绝不能**析构一个
        还在跑的 QThread (那会 abort) —— 把它从窗口的 child 关系里摘出来,
        由 main() 在退出前等它收尾。

        先把 `self._thread` 清掉: 否则关窗口 (closeEvent) 与 app 退出
        (aboutToQuit) 会把**同一个**线程各停一次 —— 5s + 5s 的 GUI 冻结,
        main() 还会对它等两遍。
        """
        self._cancel.set()
        thread = self._thread
        if thread is None:
            return
        self._thread = None          # ← 别让第二次调用再停同一个线程
        thread.quit()
        if not thread.wait(timeout_ms):
            thread.setParent(None)
            _ORPHANED_THREADS.append(thread)

    def __del__(self) -> None:
        """窗口被回收时把扫描线程停掉。

        QThread 是窗口的 child —— 窗口先没了、线程还在跑的话, 线程会被
        连带析构, 进程直接 abort (QThread: Destroyed while thread is still
        running)。解释器退出时窗口就是被回收的, 所以这条兜底必须有。

        注意: 兜底只负责「别让线程被连带析构」, **不负责等它结束** ——
        退出路径上的等待统一由 main() 做 (见 _shutdown_scan_threads)。
        """
        try:
            self._stop_scan(timeout_ms=2000)
        except Exception:
            pass


def _shutdown_scan_threads(timeout_ms: int = 5000) -> bool:
    """等所有被孤儿化的扫描线程结束。

    返回 True = 全都停了; False = 还有线程在跑 (调用方必须用 `os._exit`
    收尾, 绝不能让它被析构)。

    **列表里的引用不清** —— 清了就等于析构那个 QThread, 而它可能还在跑:
    QThread 的析构里会 qFatal("QThread: Destroyed while thread is still
    running") → 进程 abort (退出码 134)。引用要一直留着, 直到进程用
    os._exit 跳过所有析构。
    """
    for thread in list(_ORPHANED_THREADS):
        thread.wait(timeout_ms)
    return not any(thread.isRunning() for thread in _ORPHANED_THREADS)


def main() -> None:
    """Qt 版入口。

    QApplication 必须**先于**任何字体求值建好 —— 见 ui_font() 的注释。
    """
    app = QApplication.instance() or QApplication(sys.argv)
    apply_app_font(app)
    window = MainWindow()
    window.show()
    app.exec()
    # ---- 退出路径 ----
    #
    # 进程退出时**绝不能**有一个还在跑的 QThread 被析构:
    #   QThread: Destroyed while thread is still running → std::terminate
    #   → 退出码 134 (SIGABRT)
    # 两条路都会撞上:
    #   ① 解释器退出 → 模块全局被清 → 那个 QThread 的包装被回收 →
    #      C++ 侧析构一个还在跑的线程 → abort
    #   ② main() 返回后 Python 跑析构 → 同样的事
    #
    # 所以: 先等一会儿 (让 worker 把当前那个文件跑完, ffprobe 子进程
    # 能正常收尾), 然后**用 os._exit 跳过 C++ 析构**。
    # 宁可少跑一遍 atexit / Python 的清理, 也不能让进程以 134 退出。
    _shutdown_scan_threads(timeout_ms=5000)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)

