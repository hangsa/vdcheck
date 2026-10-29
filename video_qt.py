"""PySide6 版的界面层。

表格用 QAbstractTableModel + QTableView: 着色走 Qt.ForegroundRole,
tooltip 走 Qt.ToolTipRole, 虚拟滚动/列宽拖拽/横向滚动全部用 Qt 原生能力,
表格区**一个原生子窗口都没有** —— 这是「最小化恢复时表格区黑一片」的根治点
(Qt 顶层窗口是离屏渲染 + 一次性 blit, 不存在画了一半的中间态)。
"""
from __future__ import annotations

import os
import queue
import shutil
import sys
import threading
import traceback
from dataclasses import dataclass

from PySide6.QtCore import (
    QAbstractTableModel, QEvent, QModelIndex, QObject, Qt, QThread,
    QTimer, Signal, Slot,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QKeySequence, QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QDialog, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QStatusBar, QStyledItemDelegate, QTableView,
    QVBoxLayout, QWidget,
)

from video_core import (
    DEFAULT_BITRATE_KBPS, DEFAULT_SAMPLE_RATE_KHZ, EXTRACT_THUMB_W,
    VIDEO_EXTENSIONS, VideoInfo, _cell_fg, _extract_sort_key,
    _header_text_with_arrow, _next_sort_state, extract_frame, format_title,
    parse_threshold, parse_time_token, parse_video_info, run_ffprobe,
    scan_video_files, time_token_to_string, truncate_text,
)

# 着色常量**只从 video_core 取** —— 界面层不持有自己的颜色常量
# (断言: 「界面层没有自己的 FG_PASS / FG_FAIL」)。
# 用 `video_core.FG_PASS` 而不是 `from video_core import FG_PASS` ——
# 后者会把 FG_PASS 塞进 video_qt 的模块命名空间, 那条断言就红了。
import video_core  # noqa: E402


# 阈值默认值住在 video_core —— 界面层**不持有自己的字面量**,
# 与颜色的规矩一样 (见上面的 import 块)。

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


_snapshot_fail_font: QFont | None = None


def snapshot_fail_font() -> QFont:
    """失败字形 '×' 的字体: **14pt 粗体**。

    与 Tk 原版一致 (Tk 版的失败字形同样是 14 号粗体) ——
    「失败」这个状态要一眼看得见, 9pt 常规体不够显眼。

    惰性求值 (跟 ui_font() 一样): 字体度量要 QGuiApplication 在场。
    """
    global _snapshot_fail_font
    if _snapshot_fail_font is None:
        font = QFont(ui_font())
        font.setPointSize(14)
        font.setBold(True)
        _snapshot_fail_font = font
    return _snapshot_fail_font


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


# Tooltip 外观 —— 设计文档 §5.6 明写的那一条。Qt 的 tooltip 是**原生浮窗**
# (QToolTip 是个独立 widget), 它的外观只能由 app 的样式表决定。
# 这条规则只命中 QToolTip, 不带任何别的选择器 → 其它控件的绘制不受影响。
TOOLTIP_STYLESHEET = (
    'QToolTip { background-color: #ffffe0; color: black; '
    'border: 1px solid black; }'
)


def apply_app_tooltip_style(app: QApplication) -> None:
    """给原生 tooltip 套上 Tk 版那张浅黄底黑边的样式 (设计文档 §5.6)。

    Tk 版是自绘的 Tooltip Toplevel, 外观自己画的; Qt 版换成原生浮窗后,
    外观由这条样式表决定 —— 两边看着是同一种「浅黄底 + 黑边」。
    """
    app.setStyleSheet(TOOLTIP_STYLESHEET)


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
            if col.key is None:
                # 序号列: **永不染色** (规格 §3.3 / §6 #15)。
                # 注意不能把它丢给 _cell_fg —— 那个函数对**达标行**无条件返回
                # FG_PASS, 序号列会被染绿 (同一列在达标行绿、不达标行默认色,
                # 行内自己都不一致)。
                return None
            fg = _cell_fg(info, col.key)
            if fg is None:
                # 该格**不着色** → 返回 None 让 Qt 用调色板默认色。
                # 写死黑色的话深色主题下就是黑字黑底 (规格 §3.3 写的是
                # 「其余保持默认色」)。
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


# ================================================================
# 定点截图 (Task 6)
# ================================================================
#
# 行为基准: 产品文档 §3.5 与旧版的 SnapshotProgressDialog / EnlargeDialog。
#
# 两处**刻意**与主表格不同, 别统一:
#   1. 着色 —— 主表格「只红失败的那几列」; 这里不达标行的**序号格与标题格
#      整格红字** (达标行整格绿字), 见产品文档 §6 #15。
#   2. 网格用 QTableView + 委托画 QPixmap —— 不用 QTableWidget (每格一个
#      QLabel), 表格区不留子窗口。
#
# 内存: 只留 320px 的缩略图, 640px 原图用完即弃; 放大窗口要原图时
# **从磁盘重新加载** (产品文档 §6 #25/#26)。

# 抽帧存盘宽度 (640px) 与弹窗里的显示宽度 (320px) —— 现状就是「存 640 显示 320」
SNAPSHOT_FULL_W = EXTRACT_THUMB_W
SNAPSHOT_MEM_W = EXTRACT_THUMB_W // 2
# 时间点列的宽度: 缩略图 + 左右留白 (与现状的 THUMB_DISPLAY_W + 8 一致)
SNAPSHOT_CELL_W = SNAPSHOT_MEM_W + 8
# 标题列宽 —— 与主表格 title 列一致 (250px)
SNAPSHOT_TITLE_W = 250
# 序号列宽 (固定, 不随时间点数量变化)
SNAPSHOT_IDX_W = 45

# 自定义 role: 把单元格的抽帧状态递给 delegate (画面由 delegate 画)。
# delegate **只认 role** —— `SnapshotDelegate.paint` 走 `index.data(...)`,
# 不直接问 model.cell_state(); 否则这个 role 就是死的 (模型发了 dataChanged
# 信号, 画笔却压根不看它)。
SNAPSHOT_STATE_ROLE = int(Qt.UserRole) + 1


@dataclass
class CellState:
    """一个时间点单元格的抽帧状态 (存在 model 的 dict[(row, col)] 里)。

    status: 'idle' (还没轮到) | 'loading' | 'done' | 'failed'
    image_path: 抽出来的 PNG 的磁盘路径 —— 放大窗口从磁盘重新加载原图时用
    """
    status: str = 'idle'
    image_path: str | None = None


class SnapshotGridModel(QAbstractTableModel):
    """定点截图网格的模型: 行 = 文件, 列 = 序号 + 标题 + N 个时间点。

    初始只有序号 + 标题两列 —— 时间点列在「开始截图」时按 N 个时间点重建
    (与现状一致)。
    """

    def __init__(self, files: list[VideoInfo], parent=None):
        super().__init__(parent)
        self._files = list(files)
        self._tokens: list[str] = []
        self._cells: dict[tuple[int, int], CellState] = {}
        self._fm = QFontMetrics(ui_font())

    # ---- Qt 必需接口 ----

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._files)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else 2 + len(self._tokens)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if not 0 <= row < len(self._files):
            return None
        info = self._files[row]
        if role == SNAPSHOT_STATE_ROLE:
            # 时间点列: 画面由 delegate 画, 这里只把状态递过去
            return self._cells.get((row, col)) if self.is_time_column(col) else None
        if role == Qt.TextAlignmentRole:
            # **只有标题列左对齐, 其余全部居中** (产品文档 §6 #14)。
            # 不显式给这个 role 的话 QTableView 默认全左对齐 —— 与 Tk 原版
            # (序号 label anchor='center', 标题 label anchor='w') 不一致。
            return _ALIGN_FLAGS['w'] if col == 1 else _ALIGN_FLAGS['center']
        if col == 0:
            if role == Qt.DisplayRole:
                return str(row + 1)
            if role == Qt.ForegroundRole:
                return self._row_brush(info)
            return None
        if col == 1:
            if role == Qt.DisplayRole:
                return self._title(row)
            if role == Qt.ForegroundRole:
                # **整格红字** —— 不达标行的标题格整格红, 不是「只红失败列」。
                # 与主表格刻意不同 (产品文档 §6 #15)。
                return self._row_brush(info)
            if role == Qt.ToolTipRole:
                full = format_title(info)
                return full if full != self._title(row) else None
            return None
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation != Qt.Horizontal or role != Qt.DisplayRole:
            return None
        if section == 0:
            return '序号'
        if section == 1:
            return '标题'
        c = section - 2
        if 0 <= c < len(self._tokens):
            return f"T{c + 1}: {time_token_to_string(self._tokens[c])}"
        return None

    # ---- 时间点列 ----

    def set_time_tokens(self, tokens: list[str]) -> None:
        """按时间点重建列 (起跑时调用)。

        列数变了 → 整个模型 reset; 旧的单元格状态一并清掉 (列号变了,
        (row, col) 这个键不再对应同一个时间点)。
        """
        self.beginResetModel()
        self._tokens = list(tokens)
        self._cells.clear()
        self.endResetModel()

    @property
    def time_tokens(self) -> list[str]:
        return list(self._tokens)

    def is_time_column(self, section: int) -> bool:
        """该列是不是时间点列 (画面列)。"""
        return section >= 2

    def cell_state(self, row: int, col: int) -> CellState | None:
        return self._cells.get((row, col))

    def mark_loading(self, row: int, col: int) -> None:
        self._set_cell(row, col, CellState(status='loading'))

    def mark_done(self, row: int, col: int, image_path: str) -> None:
        self._set_cell(row, col, CellState(status='done',
                                           image_path=image_path))

    def mark_failed(self, row: int, col: int) -> None:
        self._set_cell(row, col, CellState(status='failed'))

    def _set_cell(self, row: int, col: int, state: CellState) -> None:
        self._cells[(row, col)] = state
        idx = self.index(row, col)
        if idx.isValid():
            self.dataChanged.emit(idx, idx, [SNAPSHOT_STATE_ROLE])

    # ---- 内部 ----

    def _row_brush(self, info: VideoInfo) -> QBrush:
        # 颜色常量从 video_core 取 (界面层不另抄一份)
        return QBrush(QColor(video_core.FG_PASS if info.is_passing
                             else video_core.FG_FAIL))

    def _title(self, row: int) -> str:
        return truncate_text(format_title(self._files[row]),
                             SNAPSHOT_TITLE_W, self._measure)

    def _measure(self, text: str) -> int:
        return self._fm.horizontalAdvance(text)


class SnapshotDelegate(QStyledItemDelegate):
    """定点截图网格的 delegate —— **自己画 QPixmap**。

    不用 QTableWidget (每格一个 QLabel): 表格区不留子窗口。

    三态 (加上起跑前的 idle):
      加载中 → 蓝色 '…'
      成功   → 320px 缩略图 (QPixmap)
      失败   → 红色 '×' (**14pt 粗体**, 与 Tk 原版一致)

    状态**只从 model 的 SNAPSHOT_STATE_ROLE 取** —— delegate 不直接问
    model.cell_state(), 否则那个自定义 role 就没人读 (模型发了 dataChanged
    信号, 画笔却压根不看它)。

    缩略图缓存在 delegate 里 —— **只留 320px 那一份**, 640px 原图
    用完即弃 (产品文档 §6 #25/#26)。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmaps: dict[tuple[int, int], QPixmap] = {}

    # ---- 缩略图缓存 ----

    def set_thumbnail(self, row: int, col: int, path: str) -> bool:
        """从磁盘加载缩略图 (主线程)。成功返回 True。

        640px 原图只在函数内存在, 缩到 320px 之后原图就被丢掉 —— 内存里
        只留显示用的那一份 (产品文档 §6 #26)。
        """
        full = QPixmap(path)
        if full.isNull():
            return False
        self._pixmaps[(row, col)] = full.scaledToWidth(
            SNAPSHOT_MEM_W, Qt.SmoothTransformation)
        return True

    def thumbnail(self, row: int, col: int) -> QPixmap | None:
        return self._pixmaps.get((row, col))

    def clear_thumbnails(self) -> None:
        self._pixmaps.clear()

    # ---- 画 ----

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        # 标题文字在 model 里已经按 250px 截断好了, 别让 Qt 再 elide 一次
        option.textElideMode = Qt.ElideNone

    def paint(self, painter, option, index):
        model = index.model()
        if not isinstance(model, SnapshotGridModel) \
                or not model.is_time_column(index.column()):
            # 序号 / 标题两列: 默认画法 (文字与颜色由 model 给)
            super().paint(painter, option, index)
            return
        state = index.data(SNAPSHOT_STATE_ROLE)
        painter.save()
        painter.fillRect(option.rect, option.palette.base())
        if state is not None and state.status == 'done':
            pixmap = self.thumbnail(index.row(), index.column())
            if pixmap is not None and not pixmap.isNull():
                self._paint_pixmap(painter, option, pixmap)
                painter.restore()
                return
        text, color = self._glyph(state)
        if state is not None and state.status == 'failed':
            # 失败 '×' 用 **14pt 粗体** —— 与 Tk 原版一致
            # (Tk 版的失败字形同样是 14 号粗体)。
            # 失败这个状态得一眼看得见, 9pt 常规体不够显眼。
            painter.setFont(snapshot_fail_font())
        painter.setPen(color)
        painter.drawText(option.rect, Qt.AlignCenter, text)
        painter.restore()

    @staticmethod
    def _glyph(state: CellState | None) -> tuple[str, QColor]:
        """状态 → (文字, 颜色)。与现状一致: 待抽是灰 '…', 抽帧中蓝 '…',
        失败红 '×'。"""
        if state is None or state.status == 'idle':
            return '…', QColor('gray')
        if state.status == 'loading':
            return '…', QColor('blue')
        if state.status == 'failed':
            return '×', QColor('red')
        return '…', QColor('gray')

    @staticmethod
    def _paint_pixmap(painter, option, pixmap: QPixmap) -> None:
        """把缩略图按比例缩到单元格里居中画。"""
        rect = option.rect.adjusted(1, 1, -1, -1)
        if rect.width() <= 0 or rect.height() <= 0:
            return
        size = pixmap.size()
        if size.width() > rect.width() or size.height() > rect.height():
            size = size.scaled(rect.size(), Qt.KeepAspectRatio)
            pixmap = pixmap.scaled(size.width(), size.height(),
                                   Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = rect.x() + (rect.width() - size.width()) // 2
        y = rect.y() + (rect.height() - size.height()) // 2
        painter.drawPixmap(x, y, pixmap)


class TimeTokenEdit(QLineEdit):
    """时间点输入框 —— 双击**只选所在的那一段** (HH / MM / SS / frame)。

    整段全选的话改一个字段就得重打一遍 (现状是分段选的, 产品文档 §3.5)。

    定位用的是 QLineEdit 自己算出来的光标位置: 双击前 Qt 必先发一次
    mousePressEvent, 那一次已经把光标放到点击处最近的字符边界上了 ——
    比自己拿像素宽度换算准 (不同平台的 QLineEdit 内边距不一样)。
    """

    def mouseDoubleClickEvent(self, event) -> None:
        self.select_segment_at_cursor()
        event.accept()

    def select_segment_at_cursor(self) -> None:
        """按当前光标位置选中所在的那一段。"""
        text = self.text()
        if not text:
            return
        idx = self.cursorPosition()
        colons = [i for i, c in enumerate(text) if c == ':']
        if not colons:
            self.setSelection(0, len(text))
            return
        if idx <= colons[0]:
            start, end = 0, colons[0]
        elif len(colons) >= 2 and idx <= colons[1]:
            start, end = colons[0] + 1, colons[1]
        elif len(colons) >= 3 and idx <= colons[2]:
            start, end = colons[1] + 1, colons[2]
        else:
            start, end = colons[-1] + 1, len(text)
        # setSelection 的第二个参数是**长度** (旧版 select_range 是右开区间)
        self.setSelection(start, end - start)


class EnlargeDialog(QDialog):
    """放大查看窗口 —— **从磁盘重新加载** 640px 原图。

    不复用网格里那张 320px 缩略图 (省内存, 产品文档 §6 #25/#26)。
    """

    def __init__(self, parent, image_path: str, title: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet(
            'QDialog { background-color: #202020; }'
            'QLabel { color: #cccccc; }')
        root = QVBoxLayout(self)
        # 原图: 重新从磁盘读 (网格里只留了 320px 的缩略图)。
        # **内存里只留这一份** —— 之前是 QImage + QPixmap.fromImage(QImage),
        # 640×360 的图存了两份 (~1.8MB); Tk 原版只留一份 PhotoImage
        # (产品文档 §6 #25: 为省内存)。
        self._pixmap = QPixmap(image_path)
        label = QLabel()
        if not self._pixmap.isNull():
            label.setPixmap(self._pixmap)
        label.setAlignment(Qt.AlignCenter)
        root.addWidget(label)
        path_label = QLabel(image_path)
        path_label.setWordWrap(True)
        root.addWidget(path_label)
        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(self.close)
        root.addWidget(close_btn)


def _snapshot_worker(files, tokens, output_dir, cancel_event, result_queue):
    """抽帧的 worker (后台线程)。

    **只往队列里放磁盘路径** —— QPixmap 非线程安全, 加载留给主线程。

    取消标志只在**格子之间**查: 已经在跑的 ffmpeg 不会被杀掉
    (产品文档 §6 #27)。
    """
    total = len(files) * len(tokens)
    done = 0
    for r, info in enumerate(files):
        if cancel_event.is_set():
            break
        for c, token in enumerate(tokens):
            if cancel_event.is_set():
                break
            result_queue.put(('cell_start', (r, c)))
            result_queue.put(('progress', (done, total, info.title)))
            try:
                seconds = parse_time_token(token, info.frame_rate_fps)
            except ValueError:
                result_queue.put(('cell_failed', (r, c)))
                done += 1
                continue
            base = os.path.splitext(info.title)[0] or 'video'
            out_name = f"{base}_t{c + 1}_{time_token_to_string(token)}.png"
            out_path = os.path.join(output_dir, out_name)
            if extract_frame(info.full_path, seconds, out_path):
                result_queue.put(('cell_done', (r, c, out_path)))
            else:
                result_queue.put(('cell_failed', (r, c)))
            done += 1
    result_queue.put(('done', (done, total, cancel_event.is_set())))


class SnapshotDialog(QDialog):
    """定点截图弹窗 (模态)。

    布局与现状一致:
      顶部: 截图地址 + 「浏览」+「开始截图」
      第二行: 时间点输入 (N 个) + 「+」
      中间: 文件 × 时间点 的网格
      底部: 状态文字 + 进度条

    抽帧跑在**普通后台线程**里 (不是 QThread): 弹窗可以在抽帧途中被关掉,
    而 QThread 随 dialog 一起析构会 abort (QThread: Destroyed while thread
    is still running)。普通 daemon 线程没有这个坑 —— 关窗后它把当前这一帧
    抽完就退出, **已启动的 ffmpeg 不会被杀** (产品文档 §6 #27)。
    """

    def __init__(self, main, files: list[VideoInfo], output_dir: str,
                 default_time_tokens: tuple[str, ...] =
                 ('00:00:07:24', '00:00:08:00')):
        super().__init__(main)
        self._main = main
        self._files = list(files)
        self._output_dir = output_dir
        self._tokens = list(default_time_tokens)
        self._state = 'idle'          # idle / running / done
        self._failed_count = 0
        self._cells_processed = 0
        self._total = len(self._files) * len(self._tokens)
        self._closed = False
        self._close_confirmed = False   # 关窗二次确认: 确认过一次就记住
        self._cancel = threading.Event()
        self._worker_thread = None
        self._result_queue: queue.Queue = queue.Queue()

        self.setWindowTitle('定点截图')
        self.setModal(True)
        self.resize(1200, 700)
        self._build_widgets()

    # ---- 界面 ----

    def _build_widgets(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(5, 5, 5, 5)
        root.setSpacing(4)

        # === 顶部: 截图地址 + 浏览 + 开始截图 ===
        row1 = QHBoxLayout()
        row1.addWidget(QLabel('截图地址:'))
        self.output_edit = QLineEdit(self._output_dir)
        row1.addWidget(self.output_edit, 1)
        browse_btn = QPushButton('浏览')
        browse_btn.clicked.connect(self._browse_output_dir)
        row1.addWidget(browse_btn)
        self.action_btn = QPushButton('开始截图')
        self.action_btn.clicked.connect(self._on_action_btn)
        row1.addWidget(self.action_btn)
        root.addLayout(row1)

        # === 第二行: 时间点输入 ===
        row2 = QHBoxLayout()
        row2.addWidget(QLabel('时间点:'))
        # 入口装在自己的 layout 里 —— 新增的入口总是排在「+」按钮前面
        self._tp_layout = QHBoxLayout()
        self._tp_layout.setContentsMargins(0, 0, 0, 0)
        self._tp_layout.setSpacing(5)
        row2.addLayout(self._tp_layout)
        add_btn = QPushButton('+')
        add_btn.setFixedWidth(30)
        add_btn.clicked.connect(lambda: self._add_time_token_entry('00:00:00:00'))
        row2.addWidget(add_btn)
        root.addLayout(row2)
        self._tp_entries: list[TimeTokenEdit] = []
        for tok in self._tokens:
            self._add_time_token_entry(tok)

        # === 中间: 网格 ===
        self.model = SnapshotGridModel(self._files, self)
        self.delegate = SnapshotDelegate(self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(self.delegate)
        self.view.setShowGrid(False)
        self.view.setSelectionMode(QAbstractItemView.NoSelection)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.clicked.connect(self._on_cell_clicked)
        root.addWidget(self.view, 1)

        # === 底部: 状态文字 + 进度条 ===
        row3 = QVBoxLayout()
        self.status_label = QLabel('就绪 — 点「开始截图」开始')
        row3.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, max(self._total, 1))
        row3.addWidget(self.progress)
        root.addLayout(row3)

        self._sync_columns()
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(100)
        self._poll_timer.timeout.connect(self._poll_queue)

    def _sync_columns(self) -> None:
        """列宽: 序号 45 / 标题 250 / 每个时间点 328。"""
        header = self.view.horizontalHeader()
        header.setMinimumSectionSize(20)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        self.view.setColumnWidth(0, SNAPSHOT_IDX_W)
        self.view.setColumnWidth(1, SNAPSHOT_TITLE_W)
        for c in range(len(self.model.time_tokens)):
            header.setSectionResizeMode(c + 2, QHeaderView.Fixed)
            self.view.setColumnWidth(c + 2, SNAPSHOT_CELL_W)

    def _add_time_token_entry(self, default: str = '00:00:00:00') -> None:
        """加一个时间点入口 (初始两个 + 「+」追加的都是这个)。"""
        edit = TimeTokenEdit(default)
        edit.setFixedWidth(110)
        self._tp_layout.addWidget(edit)
        self._tp_entries.append(edit)

    # ---- 截图地址 ----

    def _ask_directory(self, title: str) -> str:
        """选文件夹对话框 (取消时返回空串)。"""
        return QFileDialog.getExistingDirectory(self, title)

    def _browse_output_dir(self) -> None:
        """「浏览」选的是**父目录**, 下面套一层 snapshot。

        目录本身**不建** —— 等点「开始截图」才建 (产品文档 §6 #10)。
        """
        folder = self._ask_directory('选择截图存放文件夹')
        if folder:
            self.output_edit.setText(snapshot_dest_for(folder))

    # ---- 状态机 ----

    def _on_action_btn(self) -> None:
        """「开始截图」/「截图中」: idle/done → 起新一轮; running → 取消。

        取消只置标志 —— **已启动的 ffmpeg 不会被杀** (产品文档 §6 #27)。
        """
        if self._state == 'running':
            self._cancel.set()
            self.action_btn.setEnabled(False)
            return
        self._start_snap()

    def _start_snap(self) -> None:
        """校验 → 建目录 → 重建网格 → 起 worker。"""
        tokens = [e.text().strip() for e in self._tp_entries]
        tokens = [t for t in tokens if t]
        if not tokens:
            QMessageBox.warning(self, '提示', '请输入至少一个时间点。')
            return
        for tok in tokens:
            try:
                parse_time_token(tok, 30.0)      # fps 只做语法校验
            except ValueError as e:
                QMessageBox.critical(self, '错误', f"时间点格式错误: {e}")
                return
        output_dir = self.output_edit.text().strip()
        if not output_dir:
            QMessageBox.warning(self, '提示', '请先设置截图地址。')
            return
        # **建目录只有一个口子** —— 主窗口的 ensure_snapshot_dir。
        # 弹窗自己不 os.makedirs (「扫描/浏览阶段绝不建目录」那条规矩,
        # qt-snapshot 断言里有一条**模块级**扫描盯着这件事)。
        try:
            output_dir = self._main.ensure_snapshot_dir(output_dir)
        except OSError as e:
            QMessageBox.critical(self, '错误', f"无法创建截图地址:\n{e}")
            return

        self._tokens = tokens
        self.model.set_time_tokens(tokens)
        self.delegate.clear_thumbnails()
        self._sync_columns()
        self._cancel.clear()
        self._failed_count = 0
        self._cells_processed = 0
        self._total = len(self._files) * len(tokens)
        self.progress.setRange(0, max(self._total, 1))
        self.progress.setValue(0)
        self._state = 'running'
        self.action_btn.setText('截图中')
        self._set_time_points_enabled(False)
        self.output_edit.setEnabled(False)

        self._result_queue = queue.Queue()
        self._worker_thread = threading.Thread(
            target=_snapshot_worker,
            args=(self._files, tokens, output_dir, self._cancel,
                  self._result_queue),
            daemon=True,
        )
        self._worker_thread.start()
        self._poll_timer.start()

    def _set_time_points_enabled(self, enabled: bool) -> None:
        for edit in self._tp_entries:
            edit.setEnabled(enabled)

    # ---- worker 结果 ----

    def _poll_queue(self) -> None:
        """主线程 100ms 轮询: 消费 cell 状态 / 进度消息。"""
        try:
            while True:
                msg_type, payload = self._result_queue.get_nowait()
                if self._closed:
                    return
                if msg_type == 'cell_start':
                    r, c = payload
                    self.model.mark_loading(r, c + 2)
                elif msg_type == 'cell_done':
                    r, c, path = payload
                    self._mark_done(r, c + 2, path)
                elif msg_type == 'cell_failed':
                    r, c = payload
                    self._mark_failed(r, c + 2)
                elif msg_type == 'progress':
                    done, total, current = payload
                    self._update_status(done, total, current)
                elif msg_type == 'done':
                    done, total, cancelled = payload
                    self._on_snap_done(done, total, cancelled)
                    return
        except queue.Empty:
            pass

    def _mark_done(self, row: int, col: int, path: str) -> None:
        """抽帧成功 → 主线程加载 320px 缩略图 (QPixmap 非线程安全)。"""
        if not self.delegate.set_thumbnail(row, col, path):
            self._mark_failed(row, col)
            return
        self.model.mark_done(row, col, path)
        self._cells_processed += 1
        self.progress.setValue(self._cells_processed)

    def _mark_failed(self, row: int, col: int) -> None:
        self.model.mark_failed(row, col)
        self._failed_count += 1
        self._cells_processed += 1
        self.progress.setValue(self._cells_processed)

    def _update_status(self, done: int, total: int, current: str) -> None:
        if self._state != 'running':
            return
        suffix = f" ({current})" if current else ""
        self.status_label.setText(f"抽帧中 {done}/{total}{suffix}")

    def _on_snap_done(self, done: int, total: int, cancelled: bool) -> None:
        self._poll_timer.stop()
        self._state = 'idle' if cancelled else 'done'
        self.action_btn.setText('开始截图')
        self.action_btn.setEnabled(True)
        self._set_time_points_enabled(True)
        self.output_edit.setEnabled(True)
        status = '抽帧已取消' if cancelled else '抽帧完成'
        self.status_label.setText(
            f"{status}: {done}/{total} "
            f"({done - self._failed_count} 成功, {self._failed_count} 失败)")
        self.progress.setValue(self.progress.maximum())

    # ---- 点缩略图 → 放大窗口 ----

    def _on_cell_clicked(self, index) -> None:
        """点已成功的缩略图 → 放大窗口 (从磁盘重新加载原图)。"""
        if not index.isValid():
            return
        state = self.model.cell_state(index.row(), index.column())
        if state is None or state.status != 'done' or not state.image_path:
            return
        info = self._files[index.row()]
        c = index.column() - 2
        EnlargeDialog(self, image_path=state.image_path,
                      title=f"{info.title} @ T{c + 1}").show()

    # ---- 关闭 ----
    #
    # 关闭有**两条路**:
    #   窗口 X → `closeEvent` (QWidget.close 发 QCloseEvent)
    #   Esc 键 → `reject()`   (QDialog.keyPressEvent 直接调 reject)
    # 两条路都得走同一个二次确认 —— 抽帧还在跑的时候把窗关了却没确认,
    # 后台会继续往 snapshot 目录里写帧, 用户还永远看不到「抽帧完成」。
    #
    # `reject()` **不能**写成 `self.close()`: QDialog::closeEvent 内部会调
    # reject(), 那样会无限递归。

    def _confirm_close(self) -> bool:
        """抽帧中关闭 → 二次确认。返回 True = 允许关闭。

        **确认过一次就记住** —— closeEvent 与 reject() 都会调到这里
        (窗口 X 那条路上 QDialog::closeEvent 会再调一次 reject()),
        不能弹两次框。回答「否」时不记 —— 下次关还要再问一次。
        """
        if self._close_confirmed:
            return True
        if self._state == 'running':
            answer = QMessageBox.question(
                self, '确认关闭', '抽帧还在进行中, 确定要关闭吗?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return False
        self._close_confirmed = True
        return True

    def _finish_close(self) -> None:
        """确认过了 → 收尾。

        **只置取消标志**, 不 kill 已启动的 ffmpeg 进程
        (产品文档 §6 #27)。
        """
        if self._state == 'running':
            self._cancel.set()
        self._closed = True
        self._poll_timer.stop()

    def closeEvent(self, event) -> None:
        """窗口 X → 抽帧中二次确认。"""
        if not self._confirm_close():
            event.ignore()
            return
        self._finish_close()
        # QDialog::closeEvent 内部会调 reject() —— 那边已经确认过了
        # (_close_confirmed), 不会再弹一次框。
        super().closeEvent(event)

    def reject(self) -> None:
        """Esc 键 → 跟窗口 X 一样: 二次确认 + 取消抽帧。

        回答「否」→ **什么都不做** (窗还在, 抽帧还在跑)。
        """
        if not self._confirm_close():
            return
        self._finish_close()
        super().reject()


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
#
# **这张表是单一事实来源**: MainWindow._orphan_threads 是它的**同一个列表
# 对象** (类属性), 登记 (_stop_scan) 与收尾 (_shutdown_scan_threads) 走
# 的是同一张表。
#
# 为什么用**类属性**而不是直接用模块全局: CPython ≥ 3.4 的解释器收尾
# **不会**把模块全局清成 None (那是 Python 2 的行为), 所以「全局变 None
# → append 抛 AttributeError」这条理由**不成立**。真正的理由是: 类对象
# 由实例 (以及类本身) 引用着, 收尾时一定还活着 —— `__del__` 可能在一个
# 对象已经半死的时候跑, 那时走类属性比走模块全局少一层间接, 也少一个
# 「模块字典已经被清」的可能性。详见 MainWindow._orphan_threads 的注释。
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

    # 孤儿线程登记表 —— 与模块级 _ORPHANED_THREADS **同一个列表对象**。
    #
    # 为什么要挂两份 (类属性 + 模块全局): 登记 (_stop_scan) 与收尾
    # (_shutdown_scan_threads) 必须走**同一张表**, 两边各写一份迟早走神。
    # 用**类属性**做这份表的第二个入口, 是因为类对象由实例 (以及类本身)
    # 引用着, 解释器收尾时一定还活着 —— `__del__` 可能在一个对象已经半死
    # 的时候跑, 那时走类属性比走模块全局少一层间接。
    #
    # (注: CPython ≥ 3.4 **不会**在收尾时把模块全局清成 None —— 「全局变
    # None → append 抛 AttributeError」是 Python 2 时代的老传闻, 不成立。
    # 手法本身是无害的保险, 但别基于那个错前提做决定。)
    _orphan_threads = _ORPHANED_THREADS

    def __init__(self, parent=None):
        super().__init__(parent)
        # 版本号随 GUI 层一起抬到 v4.0 (GUI 层从 Tkinter 换成 PySide6)
        self.setWindowTitle('视频检测v4.0')
        self.resize(1280, 700)
        self.setMinimumSize(1000, 500)

        # 全 app 统一 9pt —— **在窗口这一层就 apply**, 不只 main() 里做:
        # 任何人直接 `MainWindow()` (断言脚本、将来的嵌入用法) 拿到的都
        # 必须是同一套字体, 否则模型按 9pt 量宽、视图按 app 默认字体渲染,
        # 预截断好的文字会溢出列宽 (清单 #2)。
        app = QApplication.instance()
        if app is not None:
            apply_app_font(app)
            apply_app_tooltip_style(app)
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

    def ensure_snapshot_dir(self, path: str | None = None) -> str:
        """截图目录**按需创建** —— 真要往里写 PNG 时才建。

        扫描 / 浏览阶段一个目录都不许建 (产品文档 §6 #10)。

        path: 弹窗里用户改过的地址 (定点截图弹窗有自己的输入框);
        省略时用主窗口的默认值 snapshot_dest。
        """
        target = (self.snapshot_dest if path is None else path).strip()
        if not target:
            raise ValueError('请先设置截图地址。')
        os.makedirs(target, exist_ok=True)
        return target

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
        # 新一轮扫描: 计数从头来。
        #
        # 更正 (2026-10-29): 旧注释写的是「漏了这一步 _passed_count 会带着
        # 上一轮的达标数累加, 状态栏出现「达标 188 · 不达标 -178」这种数」
        # —— **这个前提是错的**, 别照着它去理解代码:
        #   状态栏的「达标 N」**不是**一个跨轮次累加的运行计数器 —— 它是每轮
        #   扫描结束时由 _scan_complete **从 video_results 重新算**出来的
        #   (sum(1 for v in video_results if v.is_passing)), 算完还顺手回写
        #   self._passed_count。所以「扫第二轮会累加上一轮的达标数」这件事
        #   **根本不会发生** —— passed 是从 video_results 派生的, 而
        #   video_results 每轮都被 clear() 过, 所以 passed ≤ total 恒成立,
        #   「达标 188 · 不达标 -178」这种数**不可能出现**。
        #   → 这句 = 0 因此**不是 load-bearing** 的, 它只是双保险 (让扫描
        #   过程中 _add_result 的自增也不带着上一轮的值), 删掉它也不会让
        #   状态栏的计数出错。
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

        但**孤儿表里的线程不 deleteLater** (见下面)。
        """
        thread = self.sender()
        if thread is self._thread or self._thread is None:
            self._thread = None
            self._worker = None
        if thread is None:
            return
        # **孤儿表里的线程不 deleteLater** —— 它的 C++ 对象一旦被删,
        # 退出路径上的 thread.wait() 就会抛
        #   RuntimeError: libshiboken: Internal C++ object (Worker) already deleted.
        # 那个异常若逃出 main(), os._exit(0) 就到不了 → 解释器正常收尾 →
        # 还在跑的 QThread 被析构 → qFatal → abort (退出码 134) ——
        # 正是这条路径要防的故障。孤儿线程的引用必须一直留到进程退出。
        if thread in self._orphan_threads:
            return
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
        """打开定点截图弹窗 (模态)。

        截图目录**按需创建** —— 弹窗里点「开始截图」时才建, 而且**只**
        走 `ensure_snapshot_dir()` 这一个口子 (弹窗自己不 os.makedirs):
        「扫描/浏览阶段绝不建目录」这条规矩由那个口子兜着。

        弹窗里也有自己的「截图地址」输入框 (可以改成别的目录), 传进去的
        是主窗口当前的默认值。
        """
        dialog = SnapshotDialog(self, self.video_results, self.snapshot_dest)
        dialog.exec()

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

    def _stop_scan(self, timeout_ms: int = 0) -> None:
        """停掉扫描线程 (取消 + 等它结束)。

        **不用 QThread.terminate()** —— 目标平台是 Windows, terminate 一个
        持有 GIL 的 Python 线程会把**整个进程**挂死 (ffprobe 子进程还会
        变成孤儿)。取消标志让 worker 在**当前文件**跑完后退出循环;
        ffprobe 自带 30s 超时, 等它自然结束是安全的。

        timeout_ms 默认 **0** —— 不等: 窗口立刻就能关掉 (等 5s 的话窗口
        还在屏幕上冻着, 那是最难受的)。worker 在下一个文件边界自己退出,
        线程被摘出去交给 main() 收尾。

        等不出来 (比如 ffprobe 卡在网络盘上) 就不等了, 但**绝不能**析构一个
        还在跑的 QThread (那会 abort) —— 把它从窗口的 child 关系里摘出来,
        由 main() 在退出前等它收尾。

        先把 `self._thread` 清掉: 否则关窗口 (closeEvent) 与 app 退出
        (aboutToQuit) 会把**同一个**线程各停一次 —— GUI 冻结两遍,
        main() 还会对它等两遍。
        """
        self._cancel.set()
        thread = self._thread
        if thread is None:
            return
        self._thread = None          # ← 别让第二次调用再停同一个线程
        self._disconnect_worker()    # ← worker 可能活得比窗口久
        thread.quit()
        if not thread.wait(timeout_ms):
            thread.setParent(None)   # 先摘 parent (顺序重要)
            # 登记进孤儿表 —— **引用绝不能丢**: 引用一丢, 那个还在跑的
            # QThread 就没人持有 → 被析构 → qFatal("QThread: Destroyed
            # while thread is still running") → abort (退出码 134)。
            #
            # 用**类属性** (MainWindow._orphan_threads) 而不是模块全局:
            # 类对象由实例 (以及类本身) 引用着, 解释器收尾时一定还活着 ——
            # 而 __del__ 恰恰是在收尾时被调用的, 那时对象可能已经半死。
            # (CPython ≥ 3.4 不会把模块全局清成 None, 「append 抛
            # AttributeError」那条老理由不成立; 这里只是少一层间接。)
            self._orphan_threads.append(thread)

    def _disconnect_worker(self) -> None:
        """断开 worker → 窗口的信号连接。

        窗口没了之后 worker 还活着的话, 它发来的信号会打到已经销毁的控件上
        (PySide6 抛 RuntimeError; 出货构建的 windowed 模式 stderr 是 None →
        静默)。干脆断掉 —— 窗口都要关了, 状态栏最后那行字无所谓。

        **整个函数不许抛** —— 它被 _stop_scan 调用, 而 _stop_scan 的最后
        一步是「把线程登记进孤儿表」(绝不能被跳过)。解释器收尾时 worker 的
        C++ 对象可能已经被析构, 连 `worker.progress` 这个属性访问都可能
        抛 RuntimeError → 所以连属性访问都在 try 里。
        """
        worker = self._worker
        if worker is None:
            return
        try:
            pairs = ((worker.progress, self._on_progress),
                     (worker.result_ready, self._on_result),
                     (worker.finished, self._scan_complete),
                     (worker.cancelled, self._scan_complete))
        except Exception:
            return                       # worker 的 C++ 侧已经没了
        for signal, slot in pairs:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def __del__(self) -> None:
        """窗口被回收时把扫描线程停掉。

        QThread 是窗口的 child —— 窗口先没了、线程还在跑的话, 线程会被
        连带析构, 进程直接 abort (QThread: Destroyed while thread is still
        running)。解释器退出时窗口就是被回收的, 所以这条兜底必须有。

        注意: 兜底只负责「别让线程被连带析构」, **不负责等它结束** ——
        退出路径上的等待统一由 main() 做 (见 _shutdown_scan_threads)。

        登记孤儿线程用的是**类属性** MainWindow._orphan_threads, 不是模块
        全局 _ORPHANED_THREADS —— 类对象由实例 (以及类本身) 引用着,
        解释器收尾时一定还活着; 而 __del__ 恰恰是在收尾时被调用的, 那时
        对象可能已经半死, 走类属性比走模块全局少一层间接。
        (CPython ≥ 3.4 不会把模块全局清成 None —— 「append 抛
        AttributeError」那条老理由不成立, 别基于它做决定。)
        """
        try:
            self._stop_scan(timeout_ms=2000)
        except Exception:
            pass


def _shutdown_scan_threads(timeout_ms: int = 5000) -> None:
    """等所有被孤儿化的扫描线程结束 (尽量等, 不保证等得到)。

    **故意不返回值。** 唯一调用方是 main(), 而 main() 无论如何都要
    `os._exit(0)` —— 返回 True 还是 False, 动作一模一样, 返回值就是死 API。
    更糟的是它读起来像「True = 可以正常返回」, 而那恰恰是最危险的解读:
    正常返回会走解释器收尾 → 析构 QApplication / QThread → 撞上
    "QThread: Destroyed while thread is still running" → abort (退出码 134)。
    NEW-1/NEW-5 刚把这条坑填掉, 别再留一个看着能分支的返回值。

    需要知道「线程停了吗」就直接查: `any(t.isRunning() for t in
    _ORPHANED_THREADS)`。

    **列表里的引用不清** —— 清了就等于析构那个 QThread, 而它可能还在跑:
    QThread 的析构里会 qFatal("QThread: Destroyed while thread is still
    running") → 进程 abort (退出码 134)。引用要一直留着, 直到进程用
    os._exit 跳过所有析构。

    遍历的是 **MainWindow._orphan_threads** (与模块级 _ORPHANED_THREADS
    同一个列表对象) —— 单一事实来源: 登记 (_stop_scan) 和收尾 (这里)
    走的是同一张表, 不会出现「登记进 A 表、收尾等 B 表」的空等。
    """
    for thread in list(MainWindow._orphan_threads):
        thread.wait(timeout_ms)


def main() -> None:
    """Qt 版入口。

    QApplication 必须**先于**任何字体求值建好 —— 见 ui_font() 的注释。
    """
    app = QApplication.instance() or QApplication(sys.argv)
    apply_app_font(app)
    apply_app_tooltip_style(app)
    window = MainWindow()
    window.show()
    # ---- 退出路径 ----
    #
    # 进程退出时**绝不能**有一个还在跑的 QThread 被析构:
    #   QThread: Destroyed while thread is still running → std::terminate
    #   → 退出码 134 (SIGABRT)
    # 两条路都会撞上:
    #   ① 解释器退出 → 那个 QThread 的 Python 包装被回收 → C++ 侧析构
    #      一个还在跑的线程 → abort
    #   ② main() 返回后 Python 跑析构 → 同样的事
    #
    # 所以最后一步是 **os._exit(0)** —— 它跳过所有 C++ 析构 (也跳过
    # atexit 与 Python 的清理)。宁可少跑一遍清理, 也不能让进程以 134 退出。
    #
    # 注意**不要**在这里 flush stdout/stderr: 出货构建是 windowed
    # (PyInstaller console=False), 那边 sys.stdout / sys.stderr 是 **None**
    # (不是 NullWriter) → `sys.stdout.flush()` 抛 AttributeError → 这一行
    # 就到不了 os._exit → 解释器正常收尾 → 那个还在跑的 QThread 被析构 →
    # 又 abort 134, 而且 stderr 也是 None → traceback 静默丢弃。
    # os._exit 本来也不做 Python 层的 flush, 所以直接不 flush。
    #
    # try/finally 是**第二道保险**: 它把**整个**「进入事件循环 → 等待扫描
    # 线程结束」这段都圈进去, 保证里面任何一处抛异常, os._exit 都仍然
    # 一定执行:
    #   ① app.exec() 本身抛 (例如事件处理器里的异常一路冒到事件循环) ——
    #      不兜住的话解释器正常收尾 → 还在跑的 QThread 被析构 → qFatal →
    #      abort 134。所以 try 必须**开在 app.exec() 之前**。
    #   ② _shutdown_scan_threads 里的 thread.wait() 在极端情况下会抛
    #      (比如那个 QThread 的 C++ 对象已经被 deleteLater 掉,
    #      libshiboken 抛 RuntimeError: Internal C++ object already deleted)。
    # 那个异常一旦逃出 main(), os._exit 就到不了 → 解释器正常
    # 收尾 → 还在跑的 QThread 被析构 → qFatal → abort (退出码 134) ——
    # 正是这条路径要根治的故障。所以**无论等待过程抛不抛, os._exit 必须
    # 一定执行**。
    #
    # 正常路径与异常路径要**区分开**:
    #   正常 → finally: os._exit(0)
    #   异常 → except: 尽量落 traceback, 然后 os._exit(1)
    # 否则致命错误和正常退出长得一模一样 (退出码 0, 没有 traceback), 线上
    # 出事根本没法查。两条路都满足同一个硬约束: **进程绝不跑 Python 层
    # 析构** → 那个还在跑的 QThread 永远不会被析构 → 不会有 134。
    try:
        app.exec()
        _shutdown_scan_threads(timeout_ms=2000)
    except BaseException:
        # **先判空, 再打印**: 出货构建是 windowed (PyInstaller
        # console=False), 那边 sys.stderr 是 **None** (不是 NullWriter) ——
        # traceback.print_exc() 往 None 上写会自己抛 AttributeError → 那个
        # 异常逃出 main() → 解释器正常收尾 → 还在跑的 QThread 被析构 →
        # qFatal → abort 134, 正是这条路径要根治的故障。
        # 所以: 取一次 sys.stderr 存局部 (别让 print_exc 自己再去读全局),
        # 判 None, 再用 try 包一层 —— 打印失败也不能挡路, 最后无论如何
        # os._exit(1)。windowed 构建拿不到 traceback, 但至少**退出码 1**,
        # 足以让「干净退出」和「炸了退出」分得开。
        stderr = sys.stderr
        if stderr is not None:
            try:
                traceback.print_exc(file=stderr)
                stderr.flush()
            except BaseException:
                # 打印/flush 失败不许把路挡住 (例如 stderr 是个半死的句柄)
                pass
        # os._exit 是**立刻**终止进程的 (C 层 _exit, 不做栈展开), 所以
        # 下面的 finally 不会再执行 —— 退出码 1 是最终结果。
        os._exit(1)
    finally:
        os._exit(0)

