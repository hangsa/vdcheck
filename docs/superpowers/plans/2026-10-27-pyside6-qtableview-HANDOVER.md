# 重构交接单 — PySide6 / QTableView

> **这份文件是给「下次接手的人」看的**，包含不读代码就不知道的东西。
> 读完这一份就能从断点继续，不用把整个会话重塞进上下文。
>
> 最后更新: 2026-10-29

---

## 0. 一句话状态

表格的 **Qt 表格模型**、**Qt 主窗口**、**定点截图弹窗**（Task 6）、**入口切换 + 打包**
（Task 7）都已实现并通过审查。**下一步：最终整体审查（已派发）→ 一次统一修复波 →
Windows 实测。**

---

## 1. 位置

| | |
|---|---|
| 主 checkout | `/Users/hail/Codes/vdcheck`（**仍是 Tk 版，可正常使用**；分支 `main`，HEAD `2849dfe`） |
| 重构 worktree | `/Users/hail/Codes/vdcheck/.claude/worktrees/refactor-qtableview` |
| 分支 | `refactor/qtableview`（**未推送**） |
| HEAD | `19f4a93` |
| 提交数 | 27（`1d16bc7..HEAD`） |
| 计划 | `docs/superpowers/plans/2026-10-27-pyside6-qtableview.md` |
| 设计 spec | `docs/superpowers/specs/2026-10-27-pyside6-qtableview-design.md` |
| 产品文档 | `docs/产品文档.md`（**框架无关的行为规格 = 验收基准**） |
| SDD ledger | `.superpowers/sdd/2026-10-27-pyside6-qtableview/progress.md`（**gitignored**，在盘上） |

**所有命令都在 worktree 目录下跑。** 主 checkout 与 worktree 的 git 状态是分开的。

---

## 2. 任务进度

| 任务 | 状态 | commit | 验收 |
|---|---|---|---|
| P1 抽 `video_core.py` | ✅ | `e924966` | 审查 Approved，12 断言全绿 |
| P2 修 3 个既有缺陷 | ✅ | `d14406d` | 审查 Approved |
| P3 Qt 表格模型 | ✅ | `b175b07` | 三轮返工后 Approved，51 条断言 |
| P4 表格断言改测 Qt | ✅ | `29e2aeb` | 返工后 Approved |
| P5 Qt 主窗口 | ✅ | `d46d051` | **三轮返工**后 Approved，qt-main 126 条 / qt-table 116 条 |
| P6 Qt 定点截图弹窗 | ✅ | `19f4a93` | 审查 + 1 轮返工 + scoped 复审后通过，qt-snapshot 76 条 |
| P7 入口切换 + 打包 | ✅ | `eb229c7` | 审查 + 1 轮返工 + scoped 复审后通过 |

**当前 14 个断言脚本全绿。** `video_checker.py` 保持 CRLF（0 裸 LF）。
**P1–P7 全部完成 —— 只剩最终整体审查 + Windows 实测。**

---

## 3. 断点：下一步做什么

### 3.1 Task 6（Qt 定点截图弹窗 + 放大窗口）—— ✅ **已完成**

- commits `d46d051..19f4a93`；**审查 + 1 轮返工 + scoped 复审全过**（9 条 findings 全关）
- 实现：`SnapshotDialog` / `EnlargeDialog` / `SnapshotGridModel` / `SnapshotDelegate` /
  `TimeTokenEdit` / `CellState` + `_snapshot_worker`（`video_qt.py`）
- 断言：`2026-10-27-qt-snapshot-assertions.py`，**76 条 / 19 节**
- 弹窗网格用 `QTableView` + 自定义 delegate 画 QPixmap（**零子窗口**）
- **标题格不达标时整格红字**（与主表格「只红失败列」**刻意不同**）
- 放大窗口**从磁盘重新加载** 640px 原图（不复用内存里那张 320px）
- 抽帧中点关闭 → 二次确认；**不 kill 已启动的 ffmpeg**
- **实施过程中被 429 打断过一次**（原实施者不可恢复，换了新 agent 接手）；
  完整经过见 ledger 的「Task 6 返工轮 1/5」与「Task 6 任务审查（第 1 轮）」

### 3.2 Task 7（入口切换 + 打包）—— ✅ **已完成**（`3544fb3..eb229c7`）

- `video_checker.py` 34 行 Qt 入口（重导出 24 个名字，脚本需要的 17 个的超集；
  `video_qt` 的 import 在 `main()` **里面** → `import video_checker` 不拉 PySide6）
- Tk GUI **就地删**（2091 → 34 行）；`video_checker_tk.py` 从来没建过
- `DEFAULT_BITRATE_KBPS` / `DEFAULT_SAMPLE_RATE_KHZ` 并回 `video_core.py`，core 成唯一定义处
- `video_checker.spec`：去 tkinterdnd2；加 51 个 PySide6 `excludes`
- `build.bat`：真版本比较（3.10+）；依赖换 `PySide6`
- `CLAUDE.md` 按 Qt 架构重写；产品文档版本 v3.3 → v4.0、§9 与磁盘 14 对 14
- **产品文档 §6 的 39 行 byte-for-byte 未动**；在表格**下方**加了「Tk 版对照」注记
  （#31/#35/#36/#38/#39 → 各自的 Qt 对应）

### 3.3 **当前：最终整体审查**

- 审查包 `review-2849dfe..eb229c7.diff`（654 KB / 30 commits），**opus**
- **待裁决清单**: `.superpowers/sdd/2026-10-27-pyside6-qtableview/final-review-open-items.md`
  （A 明确缺陷 / B 死代码与文档一致性 / C 行为与资源 / D Windows 发布门禁 /
  E 已裁决不许再翻的 7 条）
- 审查回来后: **一次**统一修复波 → 一次 scoped 复审 → 裁决残余 → Windows 实测
- **Windows 实测 8 项**见 §7（其中最小化→任务栏恢复的黑屏是整个重构的靶子）

---

## 4. 关键裁决（**不读代码就不知道的东西**）

完整版在 SDD ledger；这里是要点。

| # | 裁决 | 为什么 |
|---|---|---|
| 1 | `truncate_text` 的字体度量抽象成 `measure: Callable[[str], int]` | Tk 侧传 `font.measure`、Qt 侧传 `QFontMetrics.horizontalAdvance`，截断口径完全一致，且**不用建 GUI 就能测**。但 Tk 侧要用 `_font_measure(font)` 适配器（返回 `lambda s: font.measure(s)`）—— 直接传 `font.measure` 会**立即求值**，而 bug-audit 脚本把 `_cell_font` 设成 `None`，只在快路径用 → 会 AttributeError |
| 2 | 列几何**由 Python 拥有**：表头 grid 用 `weight=0` + Python 算的宽度 | 让表头与正文逐列 Δ=0 成为**结构性**的，不依赖 Tk 的 grid 分配算法，也不依赖「grid 何时重算完」的时序 |
| 3 | 模型里没有 `_col_layout`，只有 `_widths`（section→宽度的 dict） | 计划里写错过，实现按 `_widths` 走 |
| 4 | **表头按 section 对齐不需要 `QHeaderView` 子类** | 实施者用**离屏取像素**证明 `QHeaderView` 读模型的 `TextAlignmentRole` → 在模型里给 role 就行。（推翻了我计划里的方案，但结论是对的） |
| 5 | `UI_FONT` 必须惰性求值 | `QFont()` 拷贝当前 app 的默认字体；app 不存在时字体族是空的 → `QFontMetrics` **直接 abort 进程（exit 134）**。**且 `from video_qt import UI_FONT` 仍是坑**（PEP 562 的 `__getattr__` 只保护属性访问，from-import 绑快照）→ 代码里一律用 `ui_font()` |
| 6 | 排序在**模型内**做，不用 `QSortFilterProxyModel` | 现状语义是「扫描中按插入序追加、扫完统一排一次」；proxy 会在每次插入时立刻排序 → 语义变了 |
| 7 | 排序箭头是**表头文字后缀**（`码率(kbps) ▲`），**不用** `setSortIndicator` | 产品文档 §6 第 16 条把「箭头是文字后缀、随列宽重新截断」列为**刻意保留** |
| 8 | 移动顺序 = **插入序** | 现状 `video_results`（插入序）与 `grid._data`（排序序）是两份列表，移动走前者。Qt 版只有一份 → app 侧另留一份专供移动用 |
| 9 | Tk GUI 的删除**推迟到 Task 7** | 三个 app 层脚本 import Tk 的 GUI 类，Qt 对应物要等 Task 5；Task 4 只改表格断言 |
| 10 | 「表头 x == 视图 x」是 **Qt 的 API 恒等式**（`QTableView::columnViewportPosition()` 就是 `horizontalHeader()->sectionViewportPosition()`） | 真正的对齐不变式是「表头 x == **模型** x」 |
| 11 | 列宽接线**两个方向**都要有断言 | model→view（启动时把 `_widths` 推给视图）与 view→model（用户拖表头 → `sectionResized` → `set_column_width`）。之前的脚本自己手动同时调两边 → 断言的是自己的接线 |
| 12 | `_parse_drop_data` 与它的脚本**一并删** | 它解析 tkinterdnd2 的私有格式 `{f1} {f2}`，Qt 侧走 `mimeData().urls()`（含空格路径天然正确） |
| 13 | 移动顺序 / 派生路径的斜杠不对称等 39 条「刻意保留」**一条都不能动** | 产品文档 §6 是验收清单 |
| 14 | 表头的「总宽 1130px」算式在产品文档里**是错的**（列宽之和 1040），实现按列宽表走 | 文档笔误，不是实现的错 |
| 15 | **退出路径的病根是「解释器收尾时析构还在跑的 QThread」**，承重的是 `main()` 的 `os._exit(0)` | 清引用只是触发器之一。**且** `sys.stdout`/`sys.stderr` 在打包版（`console=False`）里是 `None` → 原来那两行 flush 会抛 AttributeError → `os._exit(0)` 到不了 → **出货 exe 里 exit 134 且静默** |
| 16 | **序号列「永不染色」是刻意保留的不变量，不是「可以记一笔的行为差异」** | 产品文档 §6 #15 明写「永不染色」。**代码对齐规格，产品文档不加条目** |

---

## 5. 流程约定（下次照做）

- **派发方式**：subagent-driven。实施者 → 任务审查 → （有问题）返工轮 → scoped 复审 → 完成。
  每个任务一轮**任务审查**；返工轮后一轮 **scoped 复审**（只审那批 findings + fix diff）。
- **模型**：`sonnet` 做实施/审查；`opus` 用于大改动或架构判断。**注意**：短别名 `sonnet`
  有时解析到会话模型（MiniMax-M3），撞过 429（Token Plan 上限）；撞了就换 `fable` 重派。
- **CRLF**：`video_checker.py` 是 CRLF，**绝不能用 Python `open().read()/write()` 改**
  （整篇转 LF → 约 2000 行幽灵 diff）。只用 Edit 精确替换；改完验
  `python3 -c "d=open('video_checker.py','rb').read(); c=d.count(b'\r\n'); print('bare LF:', d.count(b'\n')-c)"`
  → 必须是 `0`。**新建的文件一律 LF**。
- **断言脚本**：`docs/superpowers/specs/<date>-<topic>-assertions.py`，LF，退出码 0 = 通过。
  没有 pytest。Tk 脚本用 `TkinterDnD.Tk()`；Qt 脚本用 `QApplication`，
  **且必须在 `from video_qt import ...` 之前建**。
- **Qt 脚本在本机无显示环境** → 用 `QT_QPA_PLATFORM=offscreen`。会打一条已知的字体族
  警告到 stderr（**来自我们自己的路径**：`QFontMetrics(QFont())` 触发的 'Sans Serif'
  不存在）—— 不在范围，但**测试环境的字宽与生产不一致**，像素级断言跑的是另一套度量。
- **改计划文件**：用 Edit 工具 + 唯一锚点。**不要用 `s.index()` 找裸标记** —— 我这么干过
  一次，匹配到的是**前一个任务**的同名 Step，把 760 行删了。改完立刻
  `grep -n "^### Task"` 校验标题还在。
- **每次派发前**：跑一遍「依赖盘点」—— 哪些断言脚本 import 了即将被删的东西。
  **不要照抄别人的结论，自己 grep 一遍**（我漏查过一次 GUI 类依赖，边界定错了）。

---

## 6. 下次开工的检查清单

1. 读这一份（不用读整个会话）
2. `cd` 到 worktree，`git log --oneline -3`，确认 HEAD 是 `eb229c7`
3. 跑一遍断言确认绿：`for f in docs/superpowers/specs/*-assertions.py; do QT_QPA_PLATFORM=offscreen python3 "$f" >/dev/null || echo "FAIL $f"; done`
   （Qt 脚本在本机需要 `QT_QPA_PLATFORM=offscreen`）
4. ~~补 Task 5 的 scoped 复审~~ → **已完成**
5. ~~Task 6 → 任务审查 → 返工轮 → scoped 复审~~ → **已完成**
6. ~~Task 7 → 任务审查 → 返工轮 → scoped 复审~~ → **已完成**
7. **最终整体审查**（opus，已派发）→ **一次**统一修复波 → 一次 scoped 复审
8. Windows 实测（§7）—— **靶子是最小化→恢复的黑屏，macOS 复现不了**

---

## 7. Windows 上必须人工确认的

| # | 项 | 怎么看 |
|---|---|---|
| 1 | **最小化 → 任务栏恢复，表格区不再黑一片** | 这是整个重构的靶子。macOS 复现不了（Aqua 是整体 blit，不逐个重绘子窗口） |
| 2 | 表格观感：文字、颜色、列宽、排序箭头、拖拽手柄 | 与 Tk 版逐项比 |
| 3 | **表头「标题」列左对齐**、其余居中 | Qt 默认全居中；实现改成在模型里给 `TextAlignmentRole`。深浅色主题各看一眼 |
| 4 | 非失败列是**默认色**不是纯黑 | 深色主题下纯黑会看不见 |
| 5 | 拖宽列 → 单元格按新宽度重新截断 | 双向接线 |
| 6 | 悬停被截断的长路径 → 弹完整值 | |
| 7 | 点「开始截图」→ 目录**这时**才建 | Task 6 接上 `ensure_snapshot_dir()` |
| 8 | 关窗口时若正在扫描 → 进程正常退出、不挂死 | Task 5 返工修的 `terminate()` 问题 |

---

## 8. 已知的、留给最终审查的 Minor

ledger（`.superpowers/sdd/.../progress.md`）里有完整清单，大意：

- `header-alignment` 脚本里 `hdr_events` 收集了载荷但从未被断言；`state` 也从未使用（死代码）
- 同文件一条断言名写反（名字说「视图跟着模型走」，条件断言的是「模型跟着视图走」）→
  **缓解**：计划里 Task 5 的措辞是对的
- 实施者报告里的探针 transcript 混了两个状态（结论正确，transcript 误导）；一处行号错
- 产品文档 §4.2 的「总宽 1130px」算式错（列宽之和是 1040）
- 实施者报告里「断言数」把结尾的「✓ 全部通过」也算进去了（差值正确，口径自洽）
- `video_qt.py` 里 4 处新注释/文档建立在一个**不存在的 Python 机制**上（「解释器收尾时模块全局被清成 None → append 抛 AttributeError」）—— 实测 3.14 上模块全局**不会**被清，且真被清也是 NameError 不是 AttributeError。注释层面，不影响行为
- 实施者报告 §6 声称「无 `__pycache__`、无未跟踪文件」不成立（worktree 根有 3 个 `.pyc`，被忽略所以工作区看着干净）
- 孤儿线程登记表**从不清理** —— 取消/重启轮次下完结的线程会累积（有界，长会话下无上限）
- `os._exit(0)` 跳过 Python 层 stdout flush —— 将来退出路径上若加 `print()` 会被静默丢弃（今天没有）
- `2026-10-23-ui-density-assertions.py` 还 import `SnapshotProgressDialog`（Tk 类）→ **Task 7 删 Tk GUI 时会断**，Task 6 负责改这一节
