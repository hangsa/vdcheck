"""PySide6 版的界面层。

表格用 QAbstractTableModel + QTableView: 着色走 Qt.ForegroundRole,
tooltip 走 Qt.ToolTipRole, 虚拟滚动/列宽拖拽/横向滚动全部用 Qt 原生能力,
表格区**一个原生子窗口都没有** —— 这是「最小化恢复时表格区黑一片」的根治点
(Qt 顶层窗口是离屏渲染 + 一次性 blit, 不存在画了一半的中间态)。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics
from PySide6.QtWidgets import QStyledItemDelegate

from video_core import (
    VideoInfo, _cell_fg, _extract_sort_key, _header_text_with_arrow,
    _next_sort_state, format_title, truncate_text,
)

# 与现状一致的字体: 9pt。**不能**跟 Qt 的 app 默认字体 (macOS 上 13pt),
# 否则所有列的视觉宽度全变、250px 的含义也变。
UI_FONT = QFont('Segoe UI', 9) if sys.platform == 'win32' else QFont()
if sys.platform != 'win32':
    UI_FONT.setPointSize(9)

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
        self._fm = QFontMetrics(UI_FONT)

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
            if col.key is None:                    # 序号列永远黑色
                return QBrush(QColor('#000000'))
            return QBrush(QColor(_cell_fg(info, col.key) or '#000000'))
        if role == Qt.TextAlignmentRole:
            return _ALIGN_FLAGS.get(col.align, int(Qt.AlignCenter))
        if role == Qt.ToolTipRole:
            if col.key is None:
                return None
            full = self._full_text(section, info)
            return full if full != self._truncated(section, info) else None
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation != Qt.Horizontal or role != Qt.DisplayRole:
            return None
        if section < 0 or section >= len(self._columns):
            return None
        col = self._columns[section]
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
        # list.sort 稳定, 同级保留插入序
        self._rows.sort(key=lambda i: _extract_sort_key(i, self.sort_key),
                        reverse=self.sort_desc)
        self.layoutChanged.emit()


class TruncatingDelegate(QStyledItemDelegate):
    """表格 delegate。

    文本在 model 的 data() 里**已经截断好**, 这里关掉 Qt 自己的 elide
    (默认是单字符 '…', 与现状的三个 ASCII 点 '...' 不一致)。
    """
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.textElideMode = Qt.ElideNone
