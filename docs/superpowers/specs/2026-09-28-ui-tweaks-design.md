# UI Tweaks: 清除记录 按钮位置 + 新增「封装」列

**日期**: 2026-09-28
**范围**: `video_checker.py`（单文件，单一 Tkinter 应用）

## 背景

`视频码率检查器` 当前窗口默认 1200×700，表格列固定 11 列，std_frame 行末尾有一颗「清除记录」按钮。本次微调:
1. 把「清除记录」按钮**留在 std_frame 行内**，但把位置从行的左端尾部改到行的右端，与上方「浏览」按钮视觉上对齐。
2. 在「视频编码」列后新增「封装」列，展示容器格式（如 mp4 / matroska / mpegts），要求**默认窗口下仍一屏装下所有列**。

## 设计

### 改动 1：「清除记录」按钮 row 内重定位

**位置**: 同一个 `std_frame`（视频检测窗口的 Row 2，含码率/采样率阈值）。

**实现**:
- 原来: `ttk.Button(std_frame, text="清除记录", command=self._clear_records).pack(side='left', padx=(15, 0))`
- 新: 把这一行改为 `side='right'`。`pack` 顺序必须在所有 `side='left'` 控件执行完毕之后调用，否则 Tk 会按逆向 pack 顺序决定右对齐基线，按钮位置不可预期。

```python
# 紧跟现有 pack 调用之后，最后一行再放清除记录
ttk.Button(std_frame, text="清除记录", command=self._clear_records).pack(
    side='right', padx=(0, 5)
)
```

**为什么是 side='right'**: Row 1 的「浏览」按钮位于 path_entry 右侧（path_entry `expand=True` 把它挤到右半边）。把 std_frame 里的「清除记录」用 `side='right'` 推到行的右端，二者大约在同一横向区域，肉眼上对齐。不改 row 数量 / 不重构 std_frame，符合用户明确要求「不移动行」。

### 改动 2：新增「封装」列

**数据流**:
1. `parse_video_info` 从 ffprobe 的 `data['format']['format_name']` 读取。该字段以逗号分隔多个格式名（如 `"mov,mp4,m4a,3gp,3g2,mj2"`）。
2. 优先在 token 列表里挑出与文件扩展名匹配的那一项；否则取第一项；format_name 缺失/空时回退到扩展名（小写、去点）；仍无则 `'N/A'`。
   - 例：mp4 文件 `format_name='mov,mp4,m4a,3gp,3g2,mj2'`, 扩展名 'mp4' 命中列表 → 显示 `mp4`（而不是 `mov`）
   - 例：mkv 文件 `format_name='matroska,webm'`, 扩展名 'mkv' 不在列表 → 取首项 `matroska`

```python
raw_format = fmt.get('format_name', '')
ext = os.path.splitext(full_path)[1].lstrip('.').lower()
container_format = ''
if raw_format:
    tokens = raw_format.split(',')
    if ext and ext in tokens:
        container_format = ext
    else:
        container_format = tokens[0]
if not container_format:
    container_format = ext if ext else 'N/A'
```

3. `VideoInfo` 增加字段 `container_format: str = 'N/A'`（默认值保证现有 `VideoInfo(...)` 构造调用全部兼容）。**字段位置必须在 `full_path` 之后**: Python `@dataclass` 规则不允许默认字段前置于无默认字段。
4. `VideoCheckerApp._create_widgets` 中修改 `columns` 元组和 `headers` dict:
   - 元组位置（line ~599）插入 `container_format`：变成 `('title', 'resolution', 'frame_rate', 'bitrate', 'video_codec', 'container_format', 'audio_codec', ...)`
   - headers（line ~604）插入：`'container_format': ('封装', 75)`
5. `VideoGridView._format_cell` 的尾部 dict 中新增键：`'container_format': info.container_format`。无需新增 `if` 分支。

**列宽策略**: 默认 75 px。`matroska`（9 字符）/ `mpegts`（7 字符）以 9pt 默认字体都能装下（每字符 ~7-14 px，上限 ~75 px）。CJK 表头「封装」2 字符 ≈ 28 px，远小于 75，标题完整显示。

### 改动 3：窗口与列宽微调

| 项 | 原 | 新 |
|---|---|---|
| `root.geometry` | `(1200, 700)` | `(1280, 700)` |
| `root.minsize` | `(900, 500)` | `(1000, 500)` |
| `title` 列宽 | 280 | 250 |
| `frame_rate` 列宽 | 80 | 75 |
| `bitrate` 列宽 | 90 | 85 |
| `result` 列宽 | 70 | 60 |
| 新列 `container_format` | — | 75 |

**装下验证**:
- 列总宽: 250 + 90 + 75 + 85 + 80 + 75 + 80 + 55 + 90 + 75 + 75 + 60 = **1090 px**
- grab 列数 = 列数 - 1 = **11** 个；总 grab 宽 = 11 × 10 px = **110 px**
- 总内容宽: **1200 px**
- `tree_frame` 内容区宽 = 1280 (root geometry) - 10 (tree_frame `pack(padx=5)`) = **1270 px 可用**
- 余量: **70 px**

注: 装下验证的精度取决于字体度量，但已有列实测都能在当前宽度正常截断；新增的「封装」列宽 75 px 也已通过历史类似列（`matroska` 类宽度）验证可行。window minsize 1000 仍能装下列（用户可继续拖动列宽自适应）。

### 不改动的事

- 拖拽逻辑、扫描线程、threshold 解析、列拖拽手柄、fg 颜色规则、`is_passing` 判定: 都保持原样
- 「达标/不达标」仍以码率 + 采样率为准；「封装」只展示，**不**进入失败着色 `failing` 集合
- VideoGridView 内部 grid 结构（data col / grab col 交替）保持不变，新增列同样遵循这一模式，`GRAB_WIDTH=10` 不动

## 错误 / 边界

- ffprobe 失败 → `parse_video_info` 返回 None，与现有逻辑一致
- `format_name` 是空字符串 → 回退到扩展名（如 `'mkv'`），仍有意义
- 文件无扩展名 → `'N/A'`，明确占位
- 扫描中用户拖更多文件 → 不影响本次改动，新增列与其他行同步刷出

## 测试计划

1. **手动启动**: `python video_checker.py` 打开应用
   - 视觉确认「清除记录」按钮出现在 std_frame 的右端，目测与「浏览」同列
   - 视觉确认表格中多了「封装」列，列头无截断
   - 默认窗口宽度 1280，所有列均可见（不需横向滚动条）
2. **数据正确性**: 用一个包含 .mp4 和 .mkv 的目录执行扫描
   - mp4 行「封装」cell = `mp4`
   - mkv 行「封装」cell = `matroska`
3. **回归**: 阈值输入、扫描/拖拽流程、「清除记录」清空行为、列拖拽手柄、状态栏文本都不应回退
4. **resize**: 把窗口缩到接近 1000 宽，所有列仍能渲染（部分长内容截断是预期内）

## 风险

- 列宽调整可能让极少数极长 title / 文件名显示为 `...`，与已有 `_truncate_text` 行为一致
- 窗口默认宽度变大致低分屏（如 1280×720 笔记本）下看不到最右列 — 但 minsize 1000 已经做了兜底，并且用户主动放大窗口后即可

## 不在范围

- 不重写 std_frame 的布局
- 不引入新的样式/主题
- 不改 `_cell_fg` 颜色规则
- 不加新测试文件
