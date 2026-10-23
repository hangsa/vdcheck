# UI 信息展示效率优化 — 设计

日期: 2026-10-23
状态: 待评审

## 背景

对 vdcheck 两个界面（主检测窗口 + 定点截图弹窗）做信息展示效率梳理后，
发现三类问题：真 bug（信息不可达）、横向空间浪费、冗余信息。

本文档定义 P0/P1/P2 共 7 项改动的具体行为与验收标准。

**不在本期范围**（已与需求方确认）：

- 「仅看不达标」筛选 — 本期不做
- 定点截图弹窗缩略图懒加载 — 本期不做（只修 P0-3 的双份内存）

## 术语

- **主界面** — `VideoCheckerApp` 主窗口
- **网格** — `VideoGridView`，主界面的自绘可滚动表格
- **弹窗** — `SnapshotProgressDialog`，定点截图界面

---

## P0 — 真 bug

### P0-1 弹窗缺横向滚动条

**现状**：`_build_table_initial`（`video_checker.py:1275-1285`）只创建纵向滚动条 `vsb`，
不创建 `hsb`，也没有绑定 Shift+滚轮。内容宽度超过窗口宽度时，
多出的部分**被裁掉且无法滚动查看**。

内容宽度（改动前，标题列按 250 px 算）：

| 时间点数 | 内容宽 | 窗口 1200 |
|---|---|---|
| 2（默认） | 951 | 余 249 |
| 3 | 1279 | **溢出 79** |
| 4 | 1607 | **溢出 407** |

用户加到第 3 个时间点时，右边图直接消失，且无任何提示。

> 注：P0-2 会让标题列变宽，2 个时间点的情形也会溢出。两个改动必须同批上线，
> 否则会短暂出现「2 个时间点也要横向滚动」的状态。见 P0-2「列宽控制」。

**改动**：`_build_table_initial` 中新增 `hsb`，pack 到 `_middle` 内 `canvas` 之下
（`side='bottom'`），并双向绑定 `xscrollcommand` / `command`。

`_inner` 的 `<Configure>` 绑定已经用 `winfo_reqwidth` 设置 scrollregion，
横向滚动所需的 scrollregion 宽度已具备，无需额外处理。

**约束**：`hsb` 的 Tk master 必须直接是 `self._middle`（它被 pack 进的容器），
**不得**使用 `pack(in_=...)` 跨容器。见
[tk-canvas-master-vs-pack-parent](../../../../.claude/projects/-Users-hail-Codes-vdcheck/memory/tk-canvas-master-vs-pack-parent.md)。

现有 `vsb` 的 master 是 `self.top` 且 pack 进 `self.top`（`:1279` / `:1284`），
已符合该约束，**不动**。

**验收**：加 4 个时间点，弹窗底部出现横向滚动条，拖到最右能看全每张图的右边缘。

---

### P0-2 弹窗标题列丢失子文件夹路径

**现状**：主界面 `_format_cell` 的 title 分支显示 `./A/B/name.mp4`（含相对路径），
弹窗 `_make_row_labels`（`:1341`）只写 `info.title`（纯文件名）。

扫子文件夹时，两个不同子目录下的同名文件在弹窗里无法区分 ——
而这正是用户要逐帧肉眼比对画面的场景。

**改动 A — 抽出共享的 `format_title()`**

```python
def format_title(info: VideoInfo) -> str:
    """标题列文本: ./A/B/name.mp4 (相对路径 + 文件名)。

    rel_path 在跨盘符时会被 parse_video_info 设成绝对全路径
    (os.path.relpath 抛 ValueError 的分支, video_checker.py:329-330),
    那种情况下 rel_path 本身已含文件名, 直接返回, 不再拼 title。
    """
    rel = info.rel_path.rstrip('/\\')
    if ntpath.isabs(rel):
        return rel
    if rel in ('', '.'):
        return f"./{info.title}"
    return f"{rel}/{info.title}"
```

主界面 `_format_cell` 的 title 分支与弹窗 `_make_row_labels` 都改调它。

**用 `ntpath` 而不是 `os.path` 是必须的**：`rel_path` 的绝对路径形态只在
Windows 产生（跨盘符 `os.path.relpath` 抛 ValueError），但断言脚本跑在 macOS 上。
`os.path.isabs('D:\\v\\a.mp4')` 在 macOS 返回 `False`，会让跨平台测试假绿。

`ntpath.isabs` 对盘符路径（`D:\...`）和 UNC 路径（`\\server\share\...`）
在两个平台行为一致，**正好覆盖该 fallback 分支可能产生的全部形态**
——因为 `rel_path = full_path` 只在 Windows 触发，`full_path` 必带盘符或 UNC。

已知边界：`ntpath.isabs('/home/u/a.mp4')` 返回 `False`（ntpath 要求盘符）。
但 POSIX 绝对路径不可能走到该 fallback 分支，所以不加 `posixpath.isabs`
兜底 —— 那是为不会发生的输入写代码。断言用例也不包含 POSIX 绝对路径。

**顺带修掉的既有 bug**：改动前的 `_format_cell`（`:1020-1022`）对绝对路径形态会输出

```
D:\videos\a.mp4/a.mp4      ← 路径出现两遍, 且分隔符混用
```

这个问题今天在主界面就存在（用户若把 D 盘文件拖进来、以 C 盘目录为 base，
`os.path.relpath` 抛 ValueError 就会命中）。抽出 `format_title` 时一并处理。

**改动 B — 弹窗标题列必须限宽**

弹窗的标题列是 `grid_columnconfigure(1, minsize=250)` 且**没有 weight**（`:1306-1307`）。
Tk grid 的列宽 = `max(minsize, 该列所有 widget 的 reqwidth)`，所以：

- 改用完整路径后，**一个深路径就能把整列撑到 400 px+**，把所有时间点列推右
- `tk.Label(width=28)` 是**最小字符数**，不是上限，挡不住这条路

必须做像素级截断 + 省略号，与主网格行为一致。为此把
`VideoGridView._truncate_text`（`:767`）提成模块级函数，字体由调用方注入
（`tkfont.Font` 需要 Tk root，不能在模块级创建）：

```python
def truncate_text(text: str, col_width: int, font: tkfont.Font) -> str:
    """按像素宽截断文本并加省略号。max_width = col_width - 10 (扣 padx + bd)。"""
```

`VideoGridView._truncate_text` 改为委托它；弹窗在 `__init__` 建一个
`tkfont.Font(font=('TkDefaultFont', 9))` 复用。

这样弹窗标题列固定 250 px，溢出内容走 `...`，完整值由 P2-1 的 tooltip 呈现
（tooltip 需同步扩展到弹窗，见 P2-1）。

**验收**：

- 扫一个含子文件夹且有同名文件的目录，弹窗标题能看出是哪个子目录的文件
- 弹窗标题列宽恒为 250 px，不因某条长路径而整体变宽
- 拖入跨盘符文件后，主界面标题列不出现路径重复

---

### P0-3 PhotoImage 原图双份驻留

**现状**：`mark_done`（`:1576-1578`）把 640 px 原图 `full` 和 320 px 缩略图 `display`
都 append 进 `self._photo_refs`。但 `full` 仅用于 `subsample()` 这一次 ——
`EnlargeDialog` 是从磁盘路径重新加载的（`:1101`），原图在整个会话期间没有其他用途。

Tk 内部按 4 字节/像素存储，640×360 约 0.9 MB/张：400 个 cell 场景下多留约 360 MB。

**改动**：删掉 `self._photo_refs.append(full)`，只保留 `display`。

**已验证**：`subsample` 返回的是独立副本，源图被 GC 后 `display` 仍完整渲染
（实测 label 需求尺寸 324×184 = 图片 320×180 + Label 默认 padx 1×2）。

**验收**：`_photo_refs` 长度 = cell 数（每 cell 一项），不再翻倍。

---

## P1 — 信息效率

### P1-1 合并「规格」列

**现状**：12 列横向占 1245 px，可用宽 1254 px，余量仅 9 px，
任何一列拖宽都触发横向滚动。其中 `container_format` / `video_codec` /
`audio_codec` 三列（合计 235 px）在批量检测场景下几乎恒定，信息密度极低。

**改动**：三列合并为一列 `spec`，显示 `封装/视频编码/音频编码`：

```
mp4/h264/aac
matroska/webm/h265
mp4/h264/N/A          ← 无音频流时（parse_video_info 的默认值就是 'N/A'，:273/:276）
```

- 列名「规格」，宽 140 px（可拖宽）
- **不可排序**：三个字段合并后排序语义无意义。与现状一致 ——
  原本这三列就不在 `_SORTABLE_COLUMNS`（`:389`）里
- 四个可排序列（`result` / `bitrate` / `frame_rate` / `audio_sample_rate`）全部保留
- `_cell_fg` 的 failing 集合 `{title, result, bitrate, audio_sample_rate}`
  不含被删的三列，染色行为不变

**改动面很小 —— 无结构性代码需要改**。所有几何逻辑都由 `len(self.columns)` 驱动：

| 位置 | 是否需改 |
|---|---|
| `_build_header` 遍历 `self.columns` | 否 |
| `_create_pool_widget` 按 `n = len(self.columns)` 建 cell + grab（`:626-643`） | 否 |
| `_compute_total_width` 按 `len(self._col_widths)` 算 grab 数（`:665-671`） | 否 |
| `_format_cell` 字典加 `spec` 分支 | **是** |
| App 的 `headers` dict | **是** |

**宽度预算**：

| | 现状 | 改后 |
|---|---|---|
| 列宽合计 | 1090 | 995 |
| 拖拽手柄 (11×10 / 9×10) | 110 | 90 |
| 序号列 | 45 | 45 |
| **合计** | **1245** | **1130** |
| 可用宽 (1280−10−16) | 1254 | 1254 |
| **余量** | **9** | **124** |

**验收**：表头 10 列，`规格` 列显示 `mp4/h264/aac` 形式；
默认窗口宽度下 9 个 grab 手柄全部可见，无需横向滚动。

---

### P1-2 单位规则统一为「header 带单位，cell 不带」

**现状**：三列三种规则。

| 列 | header | cell |
|---|---|---|
| 码率 | `码率(kbps)` | `32000` ✓ |
| 采样率 | `采样率(kHz)` | `48.0 kHz` ✗ 单位重复 |
| 帧率 | `帧率` | `25.00 fps`（单位在 cell） |

**改动**：

- 帧率表头 → `帧率(fps)`，`parse_video_info` 的 `frame_rate` 去掉 `" fps"` 后缀（`:258`）
- 采样率表头保持 `采样率(kHz)`，`audio_sample_rate` 去掉 `" kHz"` 后缀（`:285`）

**影响面**：

- 排序 key 用的是数值字段 `frame_rate_fps` / `sample_rate_khz`，**不受影响**（`:394-404`）
- `parse_time_token(tok, info.frame_rate_fps)` 用数值，**不受影响**
- `docs/superpowers/specs/2026-09-16-bug-audit-assertions.py:122` 断言
  `info.frame_rate == "30.00 fps"` → **需同步更新为 `"30.00"`**

**表头宽度核算**：`帧率(fps)` = 2 CJK × 14 + 5 ASCII × 7 ≈ 63 px < 75 px ✓

**验收**：采样率列显示 `48.0`，表头为 `采样率(kHz)`；帧率列显示 `25.00`，
表头为 `帧率(fps)`；按帧率排序结果与改动前一致。

---

### P1-3 常驻计数

**现状**：扫描完成后状态栏显示
`检测完成：共 200 个视频文件，180 个达标，20 个不达标`。
用户往下滚一屏，这行汇总就滚出视野，且无其他位置保留这些数字。

**改动**：状态栏（`:1748`）由单个 `ttk.Label` 改为 `ttk.Frame` + 两个 Label，
外层保留 `relief='sunken'` 以维持凹陷边框观感：

- 左侧（`side='left'`, `expand=True`, `anchor='w'`）：操作状态文字
- 右侧（`side='right'`, `anchor='e'`）：常驻计数 `共 N · 达标 A · 不达标 B`

`_scan_complete` 的完成文案简化为 `检测完成`（`:1964`），计数信息移交右侧。

计数更新时机：
- `_add_result`（`:1936`）— 每次插入实时递增（`N` / `A` / `B` 三个计数器累加）
- `_scan_complete`（`:1941`）— 以 `video_results` 全量重算为准
  （覆盖「未找到视频文件」等分支）
- `_clear_records`（`:1966`）— 归零为 `共 0 · 达标 0 · 不达标 0`

**不需要更新的时机**：`_move_passing_files` / `_move_all_files` 只移动磁盘文件，
不修改 `video_results`（`:1997-2035`），网格行数不变，计数保持原样。

（顺带记录的既有行为，本次不改：移动完成后列表里仍保留这些行，
看起来像「文件还在原地」。这与本期的展示效率目标无关，另行处理。）

**验收**：扫描过程中计数逐个跳；滚到列表最底部仍能看到总数；
清除记录后计数归零。

---

## P2 — 体验

### P2-1 截断文本 tooltip

**现状**：主网格标题列宽 250 px，长路径被 `_truncate_text` 截成 `abc...xyz`，
**无任何方式查看完整内容**（只能横向拖宽那一列）。P0-2 之后，弹窗标题也会被截断，
同样看不到完整路径。

**改动**：鼠标悬停在被截断的 cell 上时，弹出一个无边框 tooltip 显示完整文本。

设计要点：

- **只在确实被截断时触发**。写文本时同时记录 `cell._full_text`（完整值）与
  `cell._truncated`（布尔）
- **绑定一次性、不随滚动重建**。在 `_create_pool_widget` 里给每个 cell 绑 `<Motion>`
  （池 40 × 10 列 = 400 个绑定，建池时一次完成；`_render_visible` 只改属性不重绑）
- tooltip 用 `Toplevel(overrideredirect(True))` + `lift()`，
  避免抢焦点打断表格操作
- 鼠标移到未截断的 cell 或离开网格时立即销毁

**同时应用到弹窗**：弹窗在 `_make_row_labels` 里也记录 `_full_text` / `_truncated`，
并给标题 label 绑同样的 tooltip。弹窗的 cell 是全量创建的（非虚拟滚动），
绑定数 = 文件数，可接受。

**验收**：主网格与弹窗中，标题超长时悬停显示完整路径；未截断的 cell 悬停无 tooltip。

---

## 明确不做

| 项 | 原因 |
|---|---|
| 「仅看不达标」筛选 | 本期不做（需求方确认）。后续若做，需维护「视图索引 → 原始索引」映射以保证序号跨筛选稳定 |
| 弹窗缩略图懒加载 | P0-3 修好后内存已从约 450 MB 降到约 92 MB。本期不做 |
| `hsb` 无内容时禁用 | 与纵向滚动条的门控（`_sync_scroll_enabled`，`:840`）对称。P1-1 后默认窗口宽度已不溢出，收益小 |
| 结果列去重（文字 + 颜色） | 「达标/不达标」文字在灰度/色觉障碍场景下是必要冗余，保留 |

---

## 回归验证

已核实现有断言脚本对本次改动的敏感度：

| 脚本 | 是否受影响 | 原因 |
|---|---|---|
| `2026-09-16-bug-audit-assertions.py` | **需改** | `:122` 断言 `info.frame_rate == "30.00 fps"` |
| `2026-09-28-container-format-assertions.py` | 不受影响 | 断言 `container_format` 字段值，P1-2 改的是 `frame_rate` / `audio_sample_rate` |
| `2026-09-29-dnd-parsing-assertions.py` | 不受影响 | 纯解析函数 |
| `2026-10-13-sort-headers-assertions.py` | 不受影响 | 排序辅助函数 + `VideoInfo` 夹具（夹具里带 " fps" 字面量，但断言只看排序结果） |
| `2026-10-14-virtual-scroll-assertions.py` | 不受影响 | 自带独立 column dict（`:87`），不依赖 App 的列集合 |
| `2026-10-16-snapshot-assertions.py` | 不受影响 | 纯函数（`parse_time_token` / `extract_frame` 等） |

新增断言脚本 `2026-10-23-ui-density-assertions.py` 覆盖：

- `format_title` 全部 `rel_path` 形态：`'./'` / `'./A'` / `'./A/B'` / `'./A/B/'` /
  `''` / Windows 绝对路径 `'D:\\videos\\a.mp4'` / UNC 路径 `'\\\\srv\\share\\a.mp4'`
  —— **必须包含绝对路径且用 `ntpath` 语义**，否则 macOS 上会假绿
  —— **不要**加 POSIX 绝对路径用例：`ntpath.isabs` 对它返回 `False`，
    那是刻意的（该分支在生产中不可达）
- `truncate_text` 在纯 ASCII / 含 CJK / 超长路径下的截断结果与省略号
- `_format_cell` 的 `spec` 分支（含 `N/A` 组合）
- 新表头集合恰为 10 列，`_SORTABLE_COLUMNS` 的四个键仍全部存在于列集合中
- `_cell_fg` 行为在列合并前后不变
- `SnapshotProgressDialog` 创建后 `hsb` 存在、`xscrollcommand` 已绑定、
  且 `hsb.master is dialog._middle`

手动验收（Windows）：见各 P 项的「验收」小节。