# 重构交接单 — PySide6 / QTableView

> **这份文件是给「下次接手的人」看的**，包含不读代码就不知道的东西。
> 重构已合并到 `main`，**没有断点**；这里是「这个项目是怎么回事」的索引，
> 不是待办清单。
>
> 最后更新: 2026-10-29

---

## 0. 一句话状态

**Tk → PySide6 的重构已全部完成并合并到 `main`**（v4.0）。P1–P7 每个任务都走了
「实施者 → 任务审查 → 返工轮 → scoped 复审」，外加一次最终整体审查（opus）；
用户试用后的三轮反馈也各自修了、验过。**没有待办了。**

---

## 1. 位置

| | |
|---|---|
| 主 checkout | `/Users/hail/Codes/vdcheck`（**现在是 Qt 版 v4.0**；分支 `main`，HEAD `06ee6d8`） |
| 重构 worktree | **已删除**（合并后清理掉了） |
| 分支 | `refactor/qtableview` **已合并并删除**（本地 + 远端都删了） |
| HEAD | `06ee6d8` |
| 提交数 | 44（`1d16bc7..HEAD`） |
| 计划 | `docs/superpowers/plans/2026-10-27-pyside6-qtableview.md` |
| 设计 spec | `docs/superpowers/specs/2026-10-27-pyside6-qtableview-design.md` |
| 产品文档 | `docs/产品文档.md`（**框架无关的行为规格 = 验收基准**） |
| SDD ledger | **已删除**（按 SDD 流程，最终审查干净后清掉；关键裁决已抄进本文件 §4） |

**所有命令都在主 checkout 里跑。** 合并是快进（零冲突），合并后在 `main` 上重跑了
14 个断言脚本 —— 全绿。

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
| 试用反馈三轮 | ✅ | `c568b06` `d139eb0` `feb00c8` `f3f7d5f` | 审查 + 返工；控制器独立验证 |
| **合并到 `main`** | ✅ | `06ee6d8` | 快进；合并后重跑全量断言全绿；PR #1 MERGED |

**当前 14 个断言脚本全绿。** `video_checker.py` 保持 CRLF（0 裸 LF）。
**全部完成，没有待办。**

---

## 3. 各项的完成情况（**没有断点了**）

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

### 3.3 Task 8 —— 用户试用反馈三轮 —— ✅ **已完成**（`c568b06` / `d139eb0` / `feb00c8` / `f3f7d5f`）

- 字体 9 → **10pt**；主表格行高 **28px**；标题列 **300px**；四个可排序列各 **+10px**
- **修了一个真 bug**: 快照网格**从没设过行高** → Qt 默认 30px 把 320×180 的缩略图
  等比压成 **53×30**（只有 1/6 大小）→ 现按原尺寸 **320×180** 显示
- **修了一个截断缺陷**: `truncate_text` 快路径的宽度估算在 10pt 下不再是上界
  （`W` 实测 13px > 估算的 8px）→ 会放过超宽文本导致溢出列宽 → 改成 **13**（实测上界）
- 选中行时清掉 `State_HasFocus` → **只剩整行背景色变化，没有当前格的两条蓝竖线**
- 任务审查给出 1 Important + 5 Minor，**全部修完**；scoped 复审因 429 配额中断，
  那 6 条改由**控制器独立验证**（快路径穷举 (字符×长度) 组合无一组溢出、
  `per_char=13` 正好等于实测上界、列宽 1185px 无横向滚动条、缩略图墨迹包围盒
  恰为 320×180）→ **不再补派复审**
- 14/14 断言全绿

### 3.4 合并 —— ✅ **已完成**

- **PR #1**: https://github.com/hangsa/vdcheck/pull/1 —— GitHub 自动标记为 **MERGED**
- 合并方式: **快进**（`2849dfe` 本来就是分支的祖先 → 零冲突）
- 合并后在 `main` 上**重跑** 14 个断言脚本 → 全绿；`video_checker.py` bare LF = 0
- 清理: worktree 已删、临时分支已删（本地 + 远端）
- 最终整体审查（opus，654 KB diff）结论 *Ready to merge? With fixes* ——
  Critical 0 / Important 3 / Minor 15 → **一次统一修复波**（`b6b4af1`）+
  **两个收尾轮**（`76b297f` / `bcb316c`），**最终无开放 finding**

### 3.5 Windows 实测 —— **不再是门禁**

用户已在实机上跑过并给出三轮反馈（字体/留白/缩略图/选中样式），逐条都定位到根因
并修掉了。§7 的清单**保留作参考**（将来在 Windows 上重新打包时可以用），但它
**不再阻塞任何东西**。

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
| 17 | **不接受**实施者「序号列颜色差异往产品文档 §6 加一条」的建议 | 那等于给一个**违反不变量**的行为开口子 → 见 20 |
| 18 | 序号列颜色 → **改代码**，不改文档 | 与 17 一起，被 20 合并 |
| 19 | 退出路径的病根是「解释器收尾时析构还在跑的 QThread」，承重的是 `main()` 的 `os._exit(0)` | 「清引用」只是触发器之一。计划里那几处讲「模块全局被清成 None」的注释基于**一个不存在的机制** → 最终审查已改掉 |
| 20 | 序号列「永不染色」**代码对齐规格**，产品文档不加条目 | 复审独立核实：Tk 原版就是同时给 idx 格与 title 格上同一个色，§6 #15 讲的是**主表格** |
| 21 | **Task 6 的 brief 漏了计划依赖表里的一项范围** | 计划自己的表把 ui-density 的**弹窗部分**划归 Task 6，但自动生成的 brief 只抽 `### Task 6` 那段文字。处置：照 Task 4 先例删旧小节、语义平移 |
| 22 | 滚轮绑定那条小节**直接删，不补 Qt 版** | 逐 widget 绑 `<MouseWheel>` 是 Tk 事件派发机制的产物，`QTableView` 原生支持滚轮 |
| 23 | 快照弹窗表头纵向「不钉住」**不作保留** | 旧注释给的理由（Tk 里钉住要手工同步 x 偏移）是 Tk 构造方式的产物；Qt 里表头天然钉住 |
| 24 | `PySide6.QtNetwork` **可以排** | 导入层与 `otool -L` 链接层都证明它**不是** `QtWidgets` 的依赖 —— 推翻计划里那个未核实的猜测。**但 `QtDBus` 必须留着**（QtGui hook 的 hidden import） |
| 25 | 审查出的 8 条 Minor **并进返工轮**，不留给最终审查 | 其中 3 条是**移植保真度**问题（不是打磨），1 条是**永不可能失败的断言** |
| 26 | **产品文档 §6 的 39 行一个字都不许动** | §6 本身就是规格（验收基准），控制器无权单方面改。处置：在表格**下方**加一段「Tk 版对照」注记，说明 #31/#35/#36/#38/#39 描述的是 Tk 实现并给出 Qt 对应 |
| 27 | 三项**不进返工轮**：Windows 实测（发布门禁，不是实施者的活）/ 窗口标题 v3.3→v4.0（保留，但记进 changelog）/ 若干「留给将来」的行为差异 | 见 §8 的清单 |
| 28 | 最终审查 12 条「Declined to judge」**逐条裁决**，一条都不静默丢 | 完整版在 ledger |
| 29 | 最终残余**开一个极小的收尾轮**修掉，而不是 park | 那 3 条都便宜（一条词、一个空格、一行结构），且复审点过名 |
| 30 | `MainWindow()` / `window.show()` 留在退出路径的 `try` **之外**是**正确的** | `os._exit(0)` 这道守卫的前提是「有活着的 QThread」，而构造期**不可能**有（扫描 worker 只在用户点「开始检测」时创建）→ 复审独立确认 |

### 用户试用反馈那一轮（2026-10-29）的裁决

| # | 裁决 | 为什么 |
|---|---|---|
| 31 | 定点截图的缩略图按 **320×180** 显示（修行高 bug），**不做** 640 放大 | ① 放大窗口的意义来自「网格给 320、放大窗口给 640」——网格若直接给 640，**放大窗口就显示同一张图，变多余**；② 一格 648px 宽，5 个时间点要 3240px 屏；③ 内存 4× |
| 32 | 字号 9 → **10pt**，连带 `truncate_text` 快路径估算 7/14 → 8/16 | 快路径是「`len*per_char <= max_width` 就**不量宽度**直接返回」的**上界估算**；10pt 下 7/14 不再是上界 → 放过超宽文本 → 溢出列宽（Qt 的 elide 是关的） |
| 33 | 标题列 250 → **300px**；四个可排序列各 **+10px** | §6 的 39 条**一条都不动**（标题宽在 §4.2 不在 §6）；+10px 是**跨字体保险** —— 本机 offscreen 字体比 Windows 的 Segoe UI 窄 ~18% |
| 34 | 快照网格行高 = 缩略图高 **+ 8**（上下各 4px，与左右留白一致） | 行高**不能小于**缩略图高（180px），再小 `_paint_pixmap` 会等比缩小 → 那是「图片显示太小」那个 bug 的成因 |
| 35 | 主表格行高 = 字体高 **+ 12**（10pt 下 28px），**刻意不等于** Qt 的默认 30px | 若算出来的值正好等于 Qt 默认行高，「设了行高」与「压根没设」在断言里分不开 → 守卫断言失效 |
| 36 | 快路径的 ASCII 估算最终改成 **13**（10pt 下可打印 ASCII 的实测最大 advance，`W`=13px） | **我 brief 里那句「8/16 是真上界」是错的** —— 8px 是**平均值**不是上界；审查者实测推翻。改成真上界 + 断言用**宽字形**钉住**不变式**而不是钉住常数 |
| 37 | 选中行时清掉 `State_HasFocus`，只留整行高亮 | 蓝竖线是 Windows 风格给**当前格**画的边框（Qt 只给当前格设这个标志）；它只是**画笔提示**，不影响 currentIndex / 选中集合 / 键盘导航 |

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

**重构已全部完成并合并到 `main`。** 下面是「将来如果还要动这个仓库」的检查清单：

1. 读这一份（不用读整个会话）
2. `cd` 到主 checkout（`/Users/hail/Codes/vdcheck`），`git log --oneline -3`，
   确认 HEAD 是 `06ee6d8` 附近
3. 跑一遍断言确认绿：`for f in docs/superpowers/specs/*-assertions.py; do QT_QPA_PLATFORM=offscreen python3 "$f" >/dev/null || echo "FAIL $f"; done`
   （Qt 脚本在本机需要 `QT_QPA_PLATFORM=offscreen`）
4. ~~补 Task 5 的 scoped 复审~~ → **已完成**
5. ~~Task 6 → 任务审查 → 返工轮 → scoped 复审~~ → **已完成**
6. ~~Task 7 → 任务审查 → 返工轮 → scoped 复审~~ → **已完成**
7. ~~最终整体审查 → 统一修复波 → scoped 复审 → 两个收尾轮~~ → **已完成，无开放 finding**
8. ~~用户试用反馈三轮 → 定位根因 → 修 → 验证~~ → **已完成**
9. ~~合并到 `main`~~ → **已完成**（快进，合并后重跑全量断言全绿；PR #1 GitHub 标记 MERGED）

---

## 7. Windows 上的实测清单（**参考用，不是门禁**）

> 用户已在实机上跑过并给出三轮反馈（字体 / 留白 / 缩略图 / 选中样式），逐条都定位到根因并修掉了。下面这份清单**保留**是为了将来在 Windows 上重新打包时能快速过一遍，**不再阻塞任何东西**。

| # | 项 | 怎么看 |
|---|---|---|
| 1 | **最小化 → 任务栏恢复，表格区不再黑一片** | 这是整个重构的靶子。macOS 复现不了（Aqua 是整体 blit，不逐个重绘子窗口） |
| 2 | 表格观感：文字、颜色、列宽、排序箭头、拖拽手柄 | 与 Tk 版逐项比 |
| 3 | **表头「标题」列左对齐**、其余居中 | Qt 默认全居中；实现改成在模型里给 `TextAlignmentRole`。深浅色主题各看一眼 |
| 4 | 非失败列是**默认色**不是纯黑 | 深色主题下纯黑会看不见 |
| 5 | 拖宽列 → 单元格按新宽度重新截断 | 双向接线 |
| 5b | **选中一行 → 只有背景色变化, 没有当前格的两条蓝竖线** | Windows 风格独有, 本机 (offscreen) 复现不了 → **必须目测** |
| 6 | 悬停被截断的长路径 → 弹完整值 | |
| 7 | 点「开始截图」→ 目录**这时**才建 | Task 6 接上 `ensure_snapshot_dir()` |
| 8 | 关窗口时若正在扫描 → 进程正常退出、不挂死 | Task 5 返工修的 `terminate()` 问题 |
| 9 | **exe 冒烟**：`build.bat` 跑通 → `dist\视频码率检查器.exe` 能启动 → 扫一个目录 | 排错的 modules 排除错了，症状就是**exe 启动即崩**。先怀疑 `video_checker.spec` 的 `PYSIDE6_EXCLUDES` |
| 10 | **加一个控制台版冒烟**（`console=True`）或查 exe 的退出码 | 最终审查新增项：出货版是 `console=False` → `stderr` 是 `None` → **启动崩溃没有任何 traceback 也没有日志**，症状与「什么都没发生」无法区分。**排错时先做这一步** |
| 11 | `build.bat` 的版本检查在 cmd.exe 下实跑 | 从没在 cmd 下执行过。已知：旧版也是 LF-only 且出货过能跑的 exe，新版只加了版本检查与换 pip 包，且**无 UTF-8 BOM**（BOM 才会让 cmd 第一行就崩） |
| 12 | 记录 exe 体积（与 Tk 版对比） | |

> **第 10 项为什么重要**：出货 exe 是 windowed 模式，**崩溃是完全静默的**。
> 上面第 9 项如果只是「双击没反应」，你无从判断是启动崩了还是根本没启动。
> 先用一个 `console=True` 的构建把 traceback 拿到手。

---

## 8. 最终审查的处置结果（2026-10-29，opus）

ledger（`.superpowers/sdd/.../progress.md`）里有完整清单与逐条裁决。
最终审查结论：**Critical 0 / Important 3 / Minor 15**，"Ready to merge? **With fixes**"。
**一次修复波**做掉 3 Important + 11 Minor（清单在
`.superpowers/sdd/.../final-review-1.md`）。

**已在本波修掉**：退出路径的守卫漏洞（`try/finally` + 孤儿线程跳过 `deleteLater()`）、
**changelog**（新建 `CHANGELOG.md`）、**回归套件去掉 tkinter 依赖**、
产品文档 §4.1 的 Tk 悬空引用、恒真的表头断言、断言名写反、`hdr_events` 死代码、
`QRect` 死 import、5 处**基于不存在的 Python 机制**的注释、`run_ffprobe` 不可达的
`AttributeError` 分支、tooltip 样式表的明确决定、`ui-density` 的两个死 helper、
「扫两遍计数不累加」的行为测试（替掉源码文本断言）、`CLAUDE.md` 的 `cancel_event` 漂移。

**审查者判「not reproducible / 已解决」的**：qt-table 脚本的未用 import（零个未用）；
「20px 列宽」那条迁移后保住了原语义。

**留给将来（本波明确不做）**：
- `parse_threshold` 传非 str 会抛（公开 API 契约，无活调用方）
- 截图文件名只用 title → 不同文件夹同名视频**互相覆盖**（**既有**行为，与被删的 Tk 版
  逐字节相同 → 要改就是行为变更，该单开一条并配 changelog）
- macOS 上拖放把路径框写成反斜杠 → 自己再拒绝自己（**既有**行为，但本分支让源码在
  macOS 上能跑了 → **新可达的毛边**；值得加 `sys.platform` 守卫，**只**管路径框，
  **不动**派生路径 —— §6 #3 冻结了那个不对称）
- 拖放断言用 `.replace('\\','/')` 归一化，察觉不到上一条
- 孤儿线程登记表从不清理（实际有界 —— 关窗即结束 app）
- `os._exit(0)` 跳过 Python 层 stdout flush（今天没有 `print()`；**修退出路径时
  绝对不许加 flush** —— 打包版 stdout 是 `None`，flush 会抛并跳过 `os._exit(0)`）
- `build.bat` 是 LF-only（既有状态，出货过能跑的 exe；无 UTF-8 BOM）
