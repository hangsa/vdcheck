# 单元级不达标字段着色 — 设计

## 背景

`video_checker.py` 当前用 `ttk.Treeview` 的行级 tag 给"达标/不达标"视频上色：

```python
self.tree.tag_configure('pass', foreground='#228B22')
self.tree.tag_configure('fail', foreground='#DC143C')
```

问题：任何一个字段（码率或采样率）不达标，整行 11 个 cell 全部变红。其他字段（如分辨率、帧率、时长）明明正常，也被一并标红，反馈粒度太粗。

## 目标

只把"不达标的字段"和"对应的视频标题"标红，其余 cell 颜色保持不变（或保持整行绿色，仅当全部达标时）。

`ttk.Treeview` 的 tag 是行级的，无法对单个 cell 设不同前景色 — Tk 库本身的限制。所以需要把 Treeview 替换为自绘的网格组件。

## 颜色规则

| 场景 | 标题 cell | 码率 cell | 采样率 cell | 结果 cell | 其他 cell |
|---|---|---|---|---|---|
| 全部达标 | 绿 | 绿 | 绿 | 绿 | 绿 |
| 仅码率不达标 | 红 | 红 | 默认 | 红 | 默认 |
| 仅采样率不达标 | 红 | 默认 | 红 | 红 | 默认 |
| 两项都不达标 | 红 | 红 | 红 | 红 | 默认 |

色值：
- 绿 `#228B22`（沿用现有）
- 红 `#DC143C`（沿用现有）
- 默认色不写死，让系统决定（不同主题下默认 fg 通常为黑）

判定：
- 码率达标 ⇔ `bitrate_kbps >= bitrate_std`
- 采样率达标 ⇔ `sample_rate_passing`（已在 `parse_video_info` 计算；无音频流=True，解析失败=False）

## 架构

新增 `VideoGridView` 类，封装整张表格的 UI 职责。`VideoCheckerApp._create_widgets` 改为创建 `VideoGridView` 实例并 pack 到原来的 tree_frame。

### VideoGridView 内部

```
┌─ header_frame (Frame) ─────────────────┐
│  标题 | 分辨率 | 帧率 | 码率 | ... | 结果 │   ← 11 个 Label
└────────────────────────────────────────┘
┌─ canvas (Canvas) ──────────────────────┐
│ ┌─ data_frame (Frame, 内嵌) ──────────┐│
│ │ [row Frame: 11 个 Label]            ││
│ │ [row Frame: 11 个 Label]            ││
│ │ ...                                  ││
│ └──────────────────────────────────────┘│
└────────────────────────────────────────┘
[scrollbar Y]                              [scrollbar X]
```

- **header_frame**：11 个 `Label` 作为表头，宽高与 anchor 沿用现有 `headers` dict
- **canvas + data_frame**：所有数据行
  - 每行一个 `Frame`，行内 11 个 `Label` 横排
  - 列宽 = `headers` dict 中对应值
  - 列 anchor：title=`w`，bitrate/sample_rate/file_size=`e`，其他=`center`
- **垂直滚动条**：必须保留
- **水平滚动条**：窗口缩到 minsize（900px）时总列宽 1140px 会溢出，作为安全冗余加上
- **鼠标滚轮**：绑定 `<MouseWheel>`（Windows/macOS）+ `<Button-4>`/`<Button-5>`（Linux）

### 类接口

```python
class VideoGridView:
    def __init__(self, parent: tk.Widget, columns: dict, dnd_bind=None): ...
    def clear(self) -> None: ...
    def add_row(self, info: VideoInfo) -> None: ...
```

- `columns`：复用现有 `headers` dict 形状（`{'key': ('中文表头', width)}`）
- `dnd_bind`：可选回调，绑定到 data_frame 的 `<<Drop>>` 事件上（替代原 `self.tree.dnd_bind`）

## 数据模型变化

`VideoInfo` dataclass 新增两个字段：

```python
@dataclass
class VideoInfo:
    ...
    bitrate_std: float       # 码率阈值（kbps），扫描时写入
    sample_rate_std: float   # 采样率阈值（kHz），扫描时写入
```

原因：
- `parse_video_info` 已经知道当前阈值，最自然的位置写入
- `_add_result` 是异步触发（被 worker 线程 push 进队列），不持有当前阈值的引用
- 让 `VideoGridView.add_row(info)` 自给自足，不需要外部传参

## 数据流

### `_add_result` 改造

```python
def _add_result(self, info: VideoInfo):
    self.video_results.append(info)
    self.grid.add_row(info)
```

行构造的全部细节（11 个 Label、宽度、anchor、color 判定）都移到 `VideoGridView.add_row` 内部。

### `VideoGridView.add_row` 内部颜色判定

```python
bitrate_fails = info.bitrate_kbps < info.bitrate_std
sample_rate_fails = not info.sample_rate_passing  # 已是 bool

if info.is_passing:
    row_fg = '#228B22'
    cell_fg = lambda key: row_fg  # 全部 cell 绿
else:
    red = '#DC143C'
    default = None  # 系统默认色
    cell_fg = lambda key: red if key in (
        'title',
        'result',
        *(['bitrate'] if bitrate_fails else []),
        *(['audio_sample_rate'] if sample_rate_fails else []),
    ) else default
```

### 拖拽

```python
# 旧
self.tree.drop_target_register('DND_Files')
self.tree.dnd_bind('<<Drop>>', self._on_file_drop)

# 新
self.grid.bind_drop(self._on_file_drop)
```

`VideoGridView.bind_drop(callback)` 内部把 `DND_Files` 注册到 data_frame 上。

### 清空

```python
# 旧
for item in self.tree.get_children():
    self.tree.delete(item)

# 新
self.grid.clear()
```

`VideoGridView.clear()` 内部 `data_frame.winfo_children()` 全部 `destroy()`，并把 `canvas.yview_moveto(0)` 复位。

## 不在本期范围

- **行选中/多选**：当前代码没人调用 `tree.selection()`，跳过。未来要用到再说。
- **列排序**：当前未实现，不引入。
- **表头点击**：同上。
- **斑马纹/网格线**：视觉太重，跳过。
- **hover 反馈**：保持最小视觉。

## 错误处理 & 边界

- 空表格：扫描开始时 `clear()` 已经被调用，data_frame 无子项；canvas 高度由 `scrollregion` 自动收敛到 0，不显示空表
- 窗口缩放：canvas scrollregion 在 `data_frame` 上用 `<Configure>` 事件动态更新
- 大量行：滚动 + 滚轮绑定解决；不做虚拟化（当前规模不需要）
- 颜色对比：红/绿对色盲不友好，但这是已有的视觉语言，不在本次引入无障碍改进

## 测试

无自动化测试（项目未引入测试框架）。验证方式：

1. `python video_checker.py` 起服务
2. 准备一个测试目录，包含：
   - 全部达标视频（码率 ≥ 30000 kbps，采样率 ≥ 48 kHz）
   - 仅码率不达标（采样率 ≥ 48 kHz）
   - 仅采样率不达标（码率 ≥ 30000 kbps）
   - 两项都不达标
3. 检查每种类型 cell 颜色是否符合颜色规则表
4. 调整阈值后重新扫描，验证颜色随之变化
5. 拖拽文件验证 `<<Drop>>` 仍能触发扫描
6. 缩窗口到 minsize 验证水平滚动条出现

## 改动清单

- 新增：`VideoGridView` 类（≈ 150 行）
- 修改：`VideoInfo` dataclass 加 `bitrate_std` / `sample_rate_std`
- 修改：`parse_video_info` 写入新字段
- 修改：`_create_widgets` 用 VideoGridView 替代 Treeview 创建
- 修改：`_add_result` 改为委托 `grid.add_row`
- 修改：`_start_scan` / `_on_file_drop` 的清空逻辑用 `grid.clear()`
- 修改：删除 `tag_configure('pass'/'fail')` 调用

无新增依赖。
