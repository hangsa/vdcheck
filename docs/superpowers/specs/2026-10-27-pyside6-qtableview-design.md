# 用 PySide6 / QTableView 重写 GUI 层 — 设计

日期: 2026-10-27
状态: 待评审

**规范依据**：`docs/产品文档.md`（框架无关的行为规格，本次重写的**验收基准**）
**问题背景**：`黑窗问题.md`

---

## 1. 为什么重写

窗口最小化再恢复时，**表格区黑一小会儿**。根因已查清：

表格区不是画在 canvas 上的，是几百个**原生子窗口**（27 行 × 1 个 `ttk.Frame` + 11 个 `tk.Label` = 324 个）。Windows 上每个都是独立 HWND，恢复时系统要逐个 `ShowWindow` + 重绘，画完之前那片区域没人画 → 露底。

实测（macOS 上的事件计数，代表机制而非 Windows 数值）：

| 表格内映射的子窗口 | Map 事件 | Expose 事件 |
|---|---|---|
| 40（3 个文件） | 100 | 224 |
| 328（200 个文件） | 389 | 422 |

**已排除的方案**（`黑窗问题.md` 里的建议，逐条实测/核对过）：

| 方案 | 结论 |
|---|---|
| `<Map>` + `update_idletasks()` | **无效** —— 三种做法的重绘次数完全一样（389/422），绘制本来就没被推迟，是那 811 次绘制本身耗时 |
| 拦截 `WM_ERASEBKGND` | **做不到** —— tkinter 不暴露 `WndProc` |
| 检查 `resizable()` 引发的布局重建 | **不适用** —— 代码里没有 `resizable()` 调用 |
| CustomTkinter 的已知问题 | **不适用** —— 没用这个库 |
| 设窗体背景色 | **无对象可改** —— 实测主窗口里根本没有黑色背景，黑的是「还没画的那块区域」 |
| 启动先 `withdraw` | **有效但只治启动** —— 实测启动有 291ms 空窗口，值得单独修 |

**结论**：Tk 里没有可靠手段根治。**PySide6 从机制上解决**：QWidget 顶层窗口用 backing store（离屏位图），整个窗口渲染到离屏后**一次性 blit**，恢复时不存在「画了一半」的中间态。而 `QTableView` 用一个共享 delegate 画可见单元格，**一个子窗口都没有**。

**并且** Qt 顺手解决了几个 Tk 下的硬骨头：

| 需求 | Tk 现状 | Qt |
|---|---|---|
| 按单元格着色 | 自己实现（`_cell_fg` + `fill=`） | `Qt.ForegroundRole` 一行 |
| 单元格 tooltip | 自己按 x 查列 + 手工命中测试 | `Qt.ToolTipRole` 内置 |
| 虚拟滚动 | 自己实现 40 个 widget 的池 | `QTableView` 内置（只画可见行） |
| 列宽拖拽 | 自己画 grab 手柄 + 自己算列位置 | `QHeaderView` 内置 |
| 排序 | 自己实现三态 + 箭头 | `QSortFilterProxyModel` / 自排 + `setSortIndicator` |
| 拖入文件 | `tkinterdnd2` 的 `{f1} {f2}` 私有格式，含空格路径会切错 | `mimeData().urls()` 标准 URL，含空格天然正确 |

---

## 2. 范围

### 重写

- 主窗口（`VideoCheckerApp`）
- 主表格（`VideoGridView` → `QAbstractTableModel` + `QTableView`）
- 定点截图弹窗（`SnapshotProgressDialog`）
- 放大查看窗口（`EnlargeDialog`）
- 自绘 Tooltip（**删掉**，改用 Qt 内置）
- 线程模型（queue + 100ms 轮询 → `QThread` + 信号）
- 打包配置（`video_checker.spec` / `build.bat`）

### 保留

纯逻辑层，从 `video_checker.py` 搬进 `video_core.py`，分三类：

**① 逐字不动（15 个）**

`get_ffprobe_path` / `get_ffmpeg_path` / `VideoInfo` / `format_duration` / `format_file_size` / `parse_time_token` / `time_token_to_string` / `format_title` / `run_ffprobe` / `extract_frame` / `parse_video_info` / `_extract_sort_key` / `_next_sort_state` / `_header_text_with_arrow` / `scan_video_files`

**② 需小改（2 个）**

| 函数 | 为什么要改 |
|---|---|
| `_cell_fg` | 现在引用 `VideoGridView.FG_PASS` / `VideoGridView.FG_FAIL` —— **它不能逐字搬走**。把两个颜色常量提到 `video_core.py` 的模块级，函数体其余部分不动 |
| `truncate_text` | 签名是 `font: tkfont.Font` —— **依赖 tkinter**。字体测量换成 `QFontMetrics.horizontalAdvance`，余量/省略号/快路径估算的口径全部保留（见 §5.3） |

**③ 删除（2 个）**

| 对象 | 原因 |
|---|---|
| `_parse_drop_data` | 它解析的是 `tkinterdnd2` 私有的 `{f1} {f2}` 格式，Qt 用 `mimeData().urls()`，格式不存在了 |
| `Tooltip` 类（102 行） | Qt 的 `Qt.ToolTipRole` 内置，延迟关闭/窗口复用/`lift()` 那套是绕开 Tk 限制的手工活 |


### 明确不做

- 不改任何**用户可见语义**（`docs/产品文档.md` §6 的 37 条「刻意保留」一条都不动）
- 不引入除 PySide6 外的第三方依赖
- 不做跨平台打包（仍只出 Windows exe）
- 不修「移动后表格不刷新」等已记录为「另行处理」的问题

---

## 3. 技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 框架 | **PySide6**（不是 PyQt6） | PySide6 是 LGPL（Qt for Python 官方）；PyQt6 是 GPL/商业授权。自用工具两者都能跑，但 LGPL 更省心 |
| 版本 | PySide6 ≥ 6.7（实测 6.12） | 6.12 的 wheel 是 `cp310-abi3` → **要求 Python 3.10+**，与源码里 `X | None` 注解的要求一致 |
| 表格 | `QAbstractTableModel` + `QTableView` | `QStandardItemModel` 每格一个 `QStandardItem`，1 万行 × 10 列 = 10 万对象，内存不可接受。`QAbstractTableModel` 按需取数 |
| 排序 | **模型内排序**（不用 `QSortFilterProxyModel`） | 当前语义是「扫描中按插入序追加，扫描结束后统一排一次」。proxy 会在每次插入时立刻排序 → 语义变了。模型内排序可精确保留 |
| 截图网格 | `QTableView` + 自定义 `QStyledItemDelegate` 画图 | 不用 `QTableWidget`（每格一个 QLabel）——虽然 Qt 的 widget 不是原生窗口（不会露底），但内存浪费且违背「零子窗口」的目标 |
| 线程 | `QThread` + worker 对象 + 信号 | 比「线程 + queue + 100ms 轮询」地道，且 UI 刷新更及时 |
| 打包 | PyInstaller（沿用 `video_checker.spec`） | PySide6 的 hook 是 PyInstaller 自带的 |

---

## 4. 架构

### 4.1 文件划分

```
video_core.py       ~420 行  纯逻辑（15 个逐字搬 + 2 个小改，详见 §2）
video_checker.py   ~1900 行  全部 GUI + 入口（打包入口不变）
```

**为什么只拆两个**：现有 12 个断言脚本里，**7 个是纯逻辑的**（`from video_checker import X` 拿纯函数）。让 `video_checker.py` 显式 `from video_core import ...` 重新导出，**这 7 个一行都不用改**，回归基线直接复用。另 4 个触及 Tk 内部结构、要改写；1 个（旧拖拽格式）删除 —— 详见 §10.1。

若实施中 `video_checker.py` 超过 ~2200 行，再把截图弹窗拆出去（届时单独决策，不预先拆）。

### 4.2 数据流

```
                    ┌──────────────── 主线程 (Qt 事件循环) ────────────────┐
                    │                                                      │
  QTableView ──► VideoTableModel ──► list[VideoInfo]                       │
      ▲                ▲                                                 │
      │                │ model.rowsInserted / dataChanged                 │
      │                │                                                 │
  ScanWorker (QThread) ┴─► 信号: result_ready / progress / finished        │
                             │                                             │
                    状态栏 / 表格刷新 / 按钮态                              │
                    └──────────────────────────────────────────────────────┘
```

**数据真值有两份**（与现状一致，见 §4.5）：app 侧一份**插入序**（专供移动文件用），模型一份**显示序**（排序后）。两者是同一批 `VideoInfo` 对象的**引用**，不是拷贝。

模型持有显示序那份，worker 通过信号把新行交给主线程，主线程 `beginInsertRows` → 表格增量刷新。

### 4.3 线程模型

**现状**：worker 线程 + `queue.Queue` + 主线程每 100ms `root.after` 轮询。
**新版**：`QThread` + `ScanWorker(QObject)`，`result_ready(VideoInfo)` / `progress(str)` / `finished()` / `cancelled()` 四个信号，**queued 连接**回主线程。

**要保留的语义**：
- 结果**边扫边出**（不等扫完才显示）—— 现状如此 ✅
- 状态栏文案序列完全一致（见产品文档 §4.3）
- 扫描中点「开始检测」= 取消；worker 在每个文件循环开头检查 `isInterruptionRequested()`

**行为差异（已知、可接受）**：状态栏刷新从「每 100ms 轮询」变成「信号驱动、即时」。文案序列不变，只是更快。**changelog 记一笔**。

### 4.4 拖拽文件

Qt 的 `mimeData().urls()` 给的是标准 URL 列表，**含空格的路径天然正确**（现状在 macOS/Linux 上会被切错）。

`_parse_drop_data` 解析的是 `tkinterdnd2` 私有的 `{f1} {f2}` 格式，Qt 侧这个格式不存在了 → **函数与它的 16 用例断言脚本一并删除**。

**但覆盖不能跟着一起丢**：新增一条断言，验 Qt 侧从 `mimeData().urls()` 取路径的行为 —— 重点是**含空格的路径不被切错**（现状的缺陷）与 Windows 路径分隔符。这是**升级**而非等价改写：删掉的是旧格式的用例，加上的比旧格式更严。

> **产品文档 §6 第 35 条**（`_parse_drop_data` 不处理含空格的 macOS/Linux 路径）在 Qt 实现下**自动消失** —— 不是行为变更，是框架换了之后这个缺陷不复存在。changelog 记一笔。

### 4.5 移动顺序（现状有两份列表，Qt 版只有一份）

现状里 `VideoCheckerApp.video_results`（**插入序**）与 `VideoGridView._data`（**排序序**）是**两份独立列表**：

- 排序（`reorder`）只排 `grid._data` → **表格**显示排序后的顺序
- 移动文件遍历 `video_results` → **移动顺序与失败清单顺序是插入序**

Qt 版只有一份列表（模型的），排序会改它的顺序 → **移动顺序与失败清单顺序会变成排序序**。

**决策：保持现状**。app 侧保留一份**插入序**的列表专供移动用，模型另持一份用于显示。代价是多一份 `list[VideoInfo]` 的**引用**（不是拷贝），换来行为零差异。changelog 不必记（行为一致）。


---

## 5. 表格设计

### 5.1 模型

```python
@dataclass(frozen=True)
class ColumnSpec:
    """一列的规格。key=None 表示序号列。"""
    key: str | None      # 数据列的 key；序号列是 None
    header: str          # 表头基础文字（不含排序箭头）
    width: int           # 默认宽（px）
    align: str           # 'w' | 'center'
    sortable: bool


class VideoTableModel(QAbstractTableModel):
    def __init__(self, columns: list[ColumnSpec], parent=None):
        self._columns = columns
        self._rows: list[VideoInfo] = []       # 显示序
        self._sort_key: str | None = None
        self._sort_desc: bool = False
    # --- Qt 必需接口 ---
    def rowCount(self, parent=QModelIndex()) -> int: ...
    def columnCount(self, parent=QModelIndex()) -> int: ...
    def data(self, index, role=Qt.DisplayRole): ...
    def headerData(self, section, orientation, role=Qt.DisplayRole): ...
    # --- 内部 ---
    def append_result(self, info: VideoInfo) -> None: ...
    def clear(self) -> None: ...
    def on_header_clicked(self, section: int) -> None:
        """表头点击 → 三态循环 → 就地排序。排序态存在模型里。"""
        key = self._columns[section].key
        if key is None or not self._columns[section].sortable:
            return                                  # 不可排序列: 完全无反应
        self._sort_key, self._sort_desc = _next_sort_state(
            self._sort_key, self._sort_desc, key)
        self._apply_sort()
```

**排序态放在模型里**（现状放在 `VideoGridView._sort_key/_sort_desc`）—— 因为数据在模型里。三态循环复用 `_next_sort_state`（纯逻辑，不改）✅

**列集合 = 现状的 10 个数据列 + 1 个序号列**，key 与宽度完全照搬产品文档 §4.2。序号列是**第 0 列**（不是 Qt 的 vertical header），保持「固定 45px、不排序、不可拖宽、永不染色」。

### 5.2 着色（`Qt.ForegroundRole`）

```python
def data(self, index, role):
    if role == Qt.ForegroundRole:
        col = self._columns[index.column()]
        if col.key is None:            # 序号列
            return QBrush(Qt.black)    # 永不染色
        info = self._rows[index.row()]
        fg = _cell_fg(info, col.key)   # 复用纯逻辑层（仅颜色常量提到模块级）
        return QBrush(QColor(fg or '#000000'))
```

`_cell_fg` **原样复用**（在 `video_core.py` 里）—— 着色规则一个字都不改，只有 `VideoGridView.FG_PASS` / `FG_FAIL` 两个常量提到模块级（见 §2）。

**不画整行底色**（与现状一致：只有前景色）。

### 5.3 截断与字体

现状是**在 Python 里按像素截断**（`truncate_text`），不是靠控件 elide。Qt 的 `QTableView` 默认会 elide（且是单字符 `…`），与现状的三个 ASCII 点 `...` 不一致。

**做法**：自定义 `QStyledItemDelegate`，`setElideMode(Qt.ElideNone)`，文本在 `data()` 里就**预先截断好**返回。

**字体必须显式指定，不能跟 Qt 的 app 默认字体**。现状所有文字都是 `('TkDefaultFont', 9)`（Windows = Segoe UI 9，macOS = Helvetica 9）。Qt 的 app 默认字体在 macOS 上是 13pt —— 跟了就让**所有列的视觉宽度全变**，列宽 250px 的含义也变了。

| 平台 | 现状字体 | Qt 版要用 |
|---|---|---|
| Windows | `('TkDefaultFont', 9)` = Segoe UI 9 | `QFont('Segoe UI', 9)` |
| macOS | `('TkDefaultFont', 9)` = Helvetica 9 | `QFont('.AppleSystemUIFont', 9)` 或取系统 UI 字体再 setPointSize(9) |

`truncate_text` 的端口（`video_core.py` 里改这一处）——**把字体度量抽象成一个可调用对象**，两边共用同一份逻辑：

```python
def truncate_text(text: str, col_width: int, measure: Callable[[str], int]) -> str:
    """按像素宽截断文本并加省略号。max_width = col_width - 10 (扣 padx + bd)。

    measure: 文本像素宽度的测量函数。Tk 侧传 tkfont.Font.measure,
    Qt 侧传 QFontMetrics.horizontalAdvance —— 截断口径因此**完全一致**,
    且这个函数**不需要建 GUI 就能测** (传个 lambda 即可)。
    """
```

| | Tk 版 | Qt 版 |
|---|---|---|
| 测量 | `self._cell_font.measure` | `QFontMetrics(font).horizontalAdvance` |
| 快路径估算 | ASCII 7px/字符、CJK 14px/字符 | **保留**（同一口径） |
| 余量 | `col_width - 10` | **保留**（口径不变；实测有偏差再调） |
| 省略号 | `'...'`（三个 ASCII 点） | **保留** |

> **一致性要求**：同一平台、同一字体下，截断结果必须与现状**逐字符一致**。跨框架（不同字体的实际宽度不同）允许差异，但**口径**（余量、省略号、快路径估算）必须一致。
> 现有断言 `2026-10-23-ui-density-assertions.py` 里有截断用例 —— 该脚本属于「必须改写」的那 4 个（§10.1）。改写时**只换调用方式**（`truncate_text(t, w, font)` → `truncate_text(t, w, font.measure)`），**断言本身不动**。

### 5.4 排序

- 可排序列 4 个：`result` / `bitrate` / `frame_rate` / `audio_sample_rate`
- 排序键复用 `_extract_sort_key`（纯逻辑，不改）
- **三态循环**：无 → 升序 → 降序 → 无；切到别的列重置为升序（`_next_sort_state` 不改）
- 箭头：**保持现状的「表头文字后缀」**（`码率(kbps) ▲`），**不用** `QHeaderView.setSortIndicator`
  - 产品文档 §6 第 16 条明确把「排序箭头是表头文字后缀，会随列宽被重新截断」列为**刻意保留**的行为 → 必须保留
  - 所以表头文字 = `基础文字 + 箭头`，**再按列宽截断**（与现状 `_refresh_headers` 的顺序一致：先加箭头，再截断）
  - 实现：`headerData` 返回**已截断**的文字（用 `truncate_text` 的同一套口径）；`QHeaderView` 的 elide 设为 `Qt.ElideNone`，避免 Qt 再截一次
  - `_header_text_with_arrow` 因此**继续被使用**，排序断言脚本（`2026-10-13-sort-headers`）**一行不改**
- 排序在**模型内**做（`list.sort` + `beginResetModel`/`endResetModel`），**保存并恢复垂直滚动位置**（现状是原地重绘，滚动位置不变）
- 扫描过程中结果按插入序追加；**扫描结束后若处于排序态则统一排一次** —— 与现状一致 ✅
- 排序稳定（Python `list.sort` 稳定）✅

### 5.5 列宽

**`_col_widths` 归模型**（现状归 `VideoGridView._col_widths`）。理由：截断要用它、表头文字要用它 —— 它是「显示规格」的一部分，和数据放在一起最省事。

- `QHeaderView.setSectionResizeMode(QHeaderView.Interactive)` + 初始 `resizeSection`
- 序号列：`QHeaderView.Fixed` + `resizeSection(0, 45)`
- 用户拖动 → `sectionResized(index, old, new)` → 视图调 `model.set_column_width(index, w)` → 模型更新宽度 → **重算该列的表头文字**（加箭头后再截断）→ `headerDataChanged` + `dataChanged(该列的整列范围)`
- 用一个 `_updating_widths` 标志区分「用户拖的」与「程序设的」，避免回环
- 最小宽度 20px（`set_column_width` 里 `max(20, w)`）
- 列宽只存内存，不持久化 ✅

**已知差异**：现状最后一列**没有**拖拽手柄（9 个手柄），Qt 每列右侧都有 → 最后一列（结果）也能拖宽。无害，**changelog 记一笔**。

### 5.6 Tooltip

- `data()` 里 `Qt.ToolTipRole`：**仅当该格文本确实被截断时**返回完整文本，否则返回 `None`（不弹）—— 与现状一致 ✅
- 现状自绘的 Tooltip（102 行：延迟关闭、窗口复用、`lift()` 而非 `-topmost`）**整个删掉**，改用 Qt 内置
- 样式对齐：`app.setStyleSheet("QToolTip { background-color: #ffffe0; color: black; border: 1px solid black; }")`
- 位置：Qt 自动跟随光标（偏移与现状的 +12/+18 略有不同）—— 无害，changelog 记一笔

### 5.7 滚动

- 横向/纵向都由 `QTableView` 原生提供
- **表头与正文天然对齐**（同一个 `QTableView` 的表头）—— 现状需要两个 canvas + `xscrollcommand` 同步，**这段复杂度直接消失** ✅
- 现状「表头不随纵向滚动」的语义：Qt 表头**本来就不随纵向滚动** ✅ 天然一致

---

## 6. 定点截图弹窗

### 6.1 网格

`QTableView` + `SnapshotGridModel` + `SnapshotDelegate`：

- 行 = 文件，列 = 时间点
- 单元格三态：加载中（`…` 蓝）/ 成功（画 QPixmap）/ 失败（`×` 红）
- 状态存在 model 的 `dict[(row, col)] -> CellState`
- **委托画 QPixmap**，不用 QLabel → 零子窗口
- 表头文字 `T{c+1}: 00-00-07-24`（`time_token_to_string` 不改）
- 初始状态（未起跑）只有序号 + 标题两列 —— 与现状一致 ✅
- 起跑时按 N 个时间点重建列 —— 与现状一致 ✅
- **标题列的着色与主表格不同**（产品文档 §6 第 15 条）：弹窗里**不达标行的标题格整格红字**（不是只红失败的那几列），序号格同样跟着红。主表格是「只红失败列」，弹窗是「标题格整格红」—— 两处刻意不同，别统一
- **标题格也挂 tooltip**（`Qt.ToolTipRole` 返回完整路径，仅当被截断时）—— 与现状一致 ✅
- 标题格按 250px 截断（与主表格同一口径）

### 6.2 缩略图内存

现状：640px 原图 `subsample(2,2)` 得 320px，`_photo_refs` **只保留 display**（原图出作用域即回收）；放大窗口**从磁盘重新加载** 640px 原图。

Qt 版：delegate 持有 `QPixmap`（320px）；放大窗口从磁盘 `QImage` 重新加载 ✅ 语义一致。

> `QPixmap` 必须在 GUI 线程创建/使用（`QPixmap` 非线程安全）。worker 只产出**磁盘路径**，主线程加载 ✅ —— 现状也是这个分工。

### 6.3 时间点输入

- 默认 `00:00:07:24` / `00:00:08:00`；`+` 追加 `00:00:00:00`；**无删除按钮** ✅
- 双击只选所在段（HH / MM / SS / frame）—— Qt 里用 `QLineEdit` 的 `mouseDoubleClickEvent` 子类实现 ✅
- 格式校验用 `parse_time_token`（不改）✅

---

## 7. 主窗口

- 布局：QGridLayout / QVBoxLayout，控件与文案完全照搬产品文档 §4.1
- 「清除记录」的 `padx=(0, 272)` 硬编码 → 改成**布局对齐**（stretch/spacer），视觉一致但不再有魔数
- 「定点截图」按钮在 `kHz` 标签右侧 ✅
- 状态栏：QStatusBar，凹陷边框用 `QFrame` + `setStyleSheet`；**一行文字**，中间态只显示进度文字，终态追加统计 ✅
- 拖拽：`setAcceptDrops(True)` + `dragEnterEvent` / `dropEvent`
- 快捷键：仅 `<Control-o>` → 浏览文件夹 ✅
- **拖入文件立即开始扫描**（不需要再点「开始检测」）✅
- 「浏览」只选文件夹（`QFileDialog.getExistingDirectory`）✅

### 7.0 按钮状态机（照搬产品文档 §4.4，别简化）

- **开始检测**：`开始检测` → 点按后变 `检测中`（**仍可点**，点即取消）→ 取消瞬间变 `正在停止...` 且禁用 → 扫描结束恢复 `开始检测`
- **其余按钮从不禁用**，靠弹窗拒绝（现状如此；重写可保持或改好，但要在 changelog 写明）

### 7.0.1 「清除记录」的完整语义

点了要清：结果列表、表格、达标计数、**迁移地址**、**截图地址**、拖拽留下的文件列表。
点了**不**清：码率/采样率阈值输入框。

### 7.0.2 拖拽的拒绝规则

- 扫描中拖入 → 拒绝并提示「正在检测中，请等待完成后再添加文件。」
- 阈值非法 → 对应的错误框（与点「开始检测」同一套校验）
- 拖入的全是非视频扩展名 → 「没有找到视频文件。」
- 拖入**目录**不生效（只按扩展名过滤，目录没有扩展名）

### 7.1 派生路径

`_sync_derived_paths` 逻辑（无条件覆盖 + 迁移地址无条件转反斜杠 + 截图地址仅 win32 转）**原样保留** ✅

> **注意**：反斜杠那套是**为 Windows 观感**服务的。Qt 里 `QDir`/`QFile` 两种分隔符都能处理，但**显示**上要保持一致 → 继续按现状转。

### 7.2 目录按需创建

- 扫描/浏览阶段**绝不建目录** ✅
- 「开始截图」时才 `os.makedirs` 截图目录 ✅
- 「移动」时才 `os.makedirs` 目标目录 ✅

---

## 8. 打包

### 8.1 `video_checker.spec`

| 项 | 变更 |
|---|---|
| `datas` | 去掉 `tkinterdnd2`，保留 `ffprobe/` |
| `hiddenimports` | 去掉 `tkinterdnd2` / `tkdnd` |
| `excludes` | 排掉用不到的 Qt 模块以显著减小体积。**排除前逐个确认没用到** —— `QtNetwork` 大概率被 `QtWidgets` 依赖，排掉会构建失败。候选：`PySide6.QtQuick`、`PySide6.QtWebEngine*`、`PySide6.Qt3D*`、`PySide6.QtMultimedia*`、`PySide6.QtCharts`、`PySide6.QtDataVisualization`、`PySide6.QtPdf`、`PySide6.QtQml` |
| `console` | 保持 `False` |
| 其余 | 不变（单文件、UPX） |

### 8.2 `build.bat`

| 步骤 | 变更 |
|---|---|
| Python 版本检查 | 「3.8+」→ **「3.10+」**，并加真正的版本比较（现状只检查「有没有 python」） |
| 依赖安装 | `pip install tkinterdnd2 pyinstaller` → `pip install PySide6 pyinstaller` |
| 其余 | 不变（下载 FFmpeg → 清理 → PyInstaller） |

> **版本要求 3.10+ 有两重理由**：源码的 `X | None` 联合类型注解；PySide6 6.12 的 wheel 是 `cp310-abi3`。

---

## 9. 顺手修掉的缺陷

| # | 缺陷 | 为什么这次修 |
|---|---|---|
| 1 | `run_ffprobe` 无条件构造 Windows 专用的 `STARTUPINFO` | **不修就没法在 macOS 上开发测试**（worker 线程直接崩）。改成与 `extract_frame` 一致的 `sys.platform == 'win32'` 守卫 |
| 2 | 阈值接受 `inf` / `nan` | 顺手修（`math.isfinite` 校验），changelog 记 |
| 3 | 「未找到视频文件」文案被立刻覆盖 | 顺手修，chelog 记 |

**不修**的：移动按钮不禁用、重名链式后缀、移动后不刷新表格 —— 都记在产品文档 §8，不在本次范围。

---

## 10. 验收标准

### 10.1 行为不退化（主）

现有 12 个断言脚本分两类（按是否触碰 Tkinter 内部结构实测划分）：

**① 一行不改的（7 个）** —— 纯逻辑，与框架无关：

`2026-09-16-bug-audit` / `2026-09-28-container-format` / `2026-10-13-sort-headers` / `2026-10-16-snapshot` / `2026-10-23-sync-derived-paths` / `2026-10-26-lazy-dir-creation` / `2026-10-26-path-override`

> 前提：`video_checker.py` 显式 `from video_core import ...` 把这些名字**重新导出**，脚本的 `from video_checker import X` 不用动。
> `2026-09-29-dnd-parsing` **删除**（旧格式不存在了），换成新的 Qt URL 解析断言。

**② 必须改写的（4 个）** —— 断言了 Tk 内部结构：

| 脚本 | 触及的 Tk 内部 | 改写为 |
|---|---|---|
| `2026-10-14-virtual-scroll` | `_widget_pool` / `winfo_manager()` / `grid_columnconfigure` minsize | model 的 `rowCount` 与表格可见行数；列宽由 `_col_widths` 断言；节流语义改为「重排序在主线程一次完成」 |
| `2026-10-23-ui-density` | `_cells[i][1].cget('text')` / `_header_labels` | `model.data(index, Qt.DisplayRole)` / `model.headerData(...)`；tooltip 改为断言 `Qt.ToolTipRole` 的返回值 |
| `2026-10-26-header-alignment` | `grid_bbox` / `winfo_rootx` / `_col_layout` | 表头列 x 与模型列 x 逐列相等（**同一个 view 的表头与正文，天然 Δ=0**） |
| `2026-10-26-restore-black` | 池 widget 映射数 | **删除**。这条断言的意图是「恢复时不用逐个重绘几百个 HWND」—— Qt 的 `QTableView` 由架构保证（一个 view + 一个 delegate 画可见单元格），没有可测的对应量。**不能改成「断言通过」了事。**changelog 记「恢复露底由架构消除」 |

> `2026-10-23-ui-density` 是**混合**的：截断口径、标题格式、状态栏文案、spec 列格式、10 列集合这些用例是纯逻辑的，**保留**；只有触及 `_cells` / `_header_labels` / widget 结构的用例改写。

**改写的红线**：每条都要能说清「改写后验的是同一个用户可见语义」，**不能顺手把断言改弱**。

### 10.1.1 删除的脚本

| 脚本 | 原因 |
|---|---|
| `2026-09-29-dnd-parsing` | 测的是 `tkinterdnd2` 的 `{f1} {f2}` 私有格式，Qt 侧不存在。换成新的 Qt URL 解析断言（含空格路径） |
| `2026-10-26-restore-black` | 断言的是 Tk 的池 widget 映射数，Qt 下无对应量（见 §10.1 的红线） |

### 10.2 黑窗（本次的靶子）

Windows 上：扫 200 个文件 → 最小化 → 从任务栏恢复 → **表格区不出现黑屏**。

### 10.3 其它

- macOS 上 app **能跑起来**（修了 `run_ffprobe` 的平台守卫）
- 打包出的 exe 体积可接受（记录实际数字）
- 全部行为符合 `docs/产品文档.md`

---

## 11. 实施顺序

| 阶段 | 内容 | 可验证产物 |
|---|---|---|
| **P1** | 抽 `video_core.py`（15 个逐字搬 + 2 个小改）+ 修 `run_ffprobe` 平台守卫 + 阈值 `inf/nan` 校验。**GUI 一行不动** | **12 个断言脚本一行不改、全部仍全绿**（P1 阶段 GUI 还是 Tk，脚本测的还是 Tk 结构；`video_checker.py` 重新导出 core 的名字） |
| **P2** | 写 `VideoTableModel` + `QTableView`（含着色、排序、列宽、tooltip）+ 改写表格相关断言 + **删掉自绘 `Tooltip` 类** | 表格相关断言全绿；Tk 表格代码删除 |
| **P3** | 主窗口迁移（布局、状态栏、按钮态、拖拽、派生路径、目录按需创建）+ **删掉 `_parse_drop_data`** 与它的断言脚本，换成 Qt URL 解析断言 | 主窗口相关断言全绿；人工比对界面 |
| **P4** | 定点截图弹窗 + 放大窗口 | 截图相关断言全绿 |
| **P5** | 打包（spec + build.bat）+ Windows 实测（黑屏、exe 体积） | Windows 上人工验收清单全过 |

**P1 是关键**：它不动任何界面行为，却把「不能改的部分」和「要重写的部分」切开 —— **P1 结束时 12 个断言脚本仍然一行不改全绿**，说明这次拆分没有引入任何行为变化。后面每一步都有机器可读的回归保护。

---

## 12. 风险

| 风险 | 缓解 |
|---|---|
| 改写断言脚本时**把断言改弱**（原断言 Tk 内部结构，新断言恰好不覆盖同一语义） | 每条改写都要写清「原语义 → 新断言」对照；改完人工复核 |
| Qt 的 elide / tooltip 位置与现状视觉不同 | 已知差异清单（§5.3 §5.5 §5.6），changelog 逐条记。**排序箭头不改**（保留文字后缀，§5.4） |
| PySide6 打包后 exe 体积暴涨 | spec 的 `excludes` 排掉用不到的 Qt 模块；实测体积记进 changelog |
| 事件顺序/时序变化导致状态栏文案序列变化 | P3 的断言要逐条比对文案序列，不只是最终状态 |
| 表格「一格一 widget」换成 delegate 后，hover/选中/编辑等交互细节变化 | P2 阶段人工逐项过一遍交互清单 |
