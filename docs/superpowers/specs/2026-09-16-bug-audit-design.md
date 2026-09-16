# Bug 审计 — 设计

## 背景

`video_checker.py`（937 行单文件 Tkinter 桌面应用）最近 9 次 commit 全是 UI 重构（Treeview → VideoGridView → 按 cell 着色 → 列宽可拖动 → 截断算法 30x 提速）。代码量已翻倍，UI 层从「标准组件」变成「自绘 Canvas 网格」，线程模型、列结构、拖拽绑定都经历了多轮返工。

与此同时，`AGENTS.md` 已记录两条硬问题（macOS/Linux 上 `subprocess.STARTUPINFO` 崩溃、Python 3.10+ 与 build.bat 描述不一致），但没有系统梳理过其他问题。本次审计把这些潜在问题一次性摸清楚，**只记录不修复**，便于下一轮决定哪些值得动手。

## 范围

**只审计 Bug 与正确性问题**：
- 崩溃 / 未捕获异常
- 数据正确性（移动后状态、并发状态）
- 线程与并发安全
- 输入解析与边界
- 业务逻辑（阈值判定、采样率语义、扫描-移动交互）
- 资源清理（队列、绑定、内存）

**不审计**：
- 代码风格、可测试性、架构边界
- 性能（除非是 bug 衍生，例如 N² 截断）
- 安全（已在 AGENTS.md「安全注意事项」中记录）
- 构建 / 打包

**不实施**：
- 不修复任何 bug
- 不添加测试框架
- 不重构、不引入新依赖

## 方法

### 静态走查

按文件顺序通读 `video_checker.py`，按下方「检查清单」六类逐项过。937 行预计 30–60 分钟人工走查。

### 纯函数断言（动态验证）

对以下纯函数构造 fixture 用 `assert` 验证推断：
- `scan_video_files(directory, recursive)` — 临时目录 + 嵌套 + 非视频文件
- `parse_video_info(data, …)` — 手工构造 ffprobe JSON 覆盖各分支：
  - 无视频流 / 无音频流
  - 码率仅 stream 级 / 仅 format 级 / 两处都有（优先级）
  - `r_frame_rate = "0/0"` 边界
  - 异常 tags（大小写、缺失）
  - 文件大小缺失时回退 `os.path.getsize`
  - 跨平台 `rel_path`（`/` vs `\`）
- `_cell_fg(info, key)` — 12 种 (is_passing, bitrate_fail, sr_fail) × key 组合
- `_truncate_text(text, col_width)` — 短文本快路径、长文本二分、纯 ASCII vs CJK、空字符串
- `format_duration(seconds)` / `format_file_size(bytes)` — 边界
- `_move_passing_files` / `_get_unique_dest` — 临时目录，重名场景

断言脚本一次性运行、不进入版本库，作为 spec 附录存在。**所有断言失败都需要在 spec 中专门记录（"静态推断与实际行为不符"）**。

### GUI / 线程部分

只静态分析。本机 macOS 因 `subprocess.STARTUPINFO` 直接崩溃（已知），无法启动扫描；UI 状态机靠代码读 + git log 反推。

## 检查清单（六类）

### 1. 崩溃（未捕获异常）

- `run_ffprobe` 在 macOS/Linux 上 `subprocess.STARTUPINFO` `AttributeError` — AGENTS.md 已记录，需在 spec 中给出最小复现与影响范围
- `parse_video_info` 各字段的 `try/except` 是否覆盖全部边界
- `_on_file_drop` 中 `re.findall(r'\{([^}]+)\}', …)` 对含 `}` 的路径截断后是否会传空字符串
- `_start_scan` 中 `directory` 是文件而非目录 / 权限不足时是否有兜底

### 2. 数据正确性

- `shutil.move` 后 `video_results` 列表与磁盘不同步，表格残留、再次移动会失败
- 移动目标目录是源目录的子目录（用户拖到 `dest = ./Checked`，扫描目录又是 `./Checked` 子集）→ 循环移动 / 数据丢失
- `_get_unique_dest` 重名追加 `_1`、`_2`，但若原文件已是 `name_1.ext` 命名，会变成 `name_1_1.ext`（语义丢失）
- 拖拽文件 `_scan_files_worker` 用 `os.path.dirname(files[0])` 作 base_path，单文件路径就是 `/`，导致 `rel_path` 全为 `./` —— 多个不同子目录的文件在表格里无法区分
- `os.walk` 默认不跟随符号链接 → 不构成安全/正确性问题（避免循环），但需确认这是有意

### 3. 并发 / 线程

- `threading.Thread(...)` 不保存引用，daemon 线程在主线程不引用时是否会被 GC（Python 文档说明：只要 `start()` 已调用、daemon=False 则会保留；但 daemon=True 是否仍被 GC？需核实）
- `_check_queue` 只在 `self.scanning` 为 True 时 `after(100, ...)`，若 worker 异常未发出 `done`，UI 永久停摆
- 扫描中未禁用 `move_btn` / `move_all_btn` —— 用户可点击
- `_check_queue` 不在 done 后重排，但 `self.scanning` 只在 done 时复位 → 异常路径下状态机卡死
- `bind_all` 在 `_Leave` 时解绑，但快速 mouse-hover-out 切换焦点可能漏解 → 滚轮泄漏到全局

### 4. 输入解析

- 拖拽 Windows 格式 `\{path with space\}`，但 macOS/Linux 拖拽格式是 `file://` URL，无大括号 → 当前 `re.findall` 拿不到，落到 `file_list = [files]` 把整串当文件路径
- 单文件拖拽无大括号时整串当路径，相对路径基准（第 2 类已列）
- 路径前后空白未 `strip()`
- `bitrate_std` / `sample_rate_std` 接受任意正浮点数，包括 `inf`、`nan`（Python float）
- 文件扩展名匹配走 `os.path.splitext(f)[1].lower()` —— 但 `.tar.gz` 类双扩展名会被错误排除（不一定是 bug，但需记录）

### 5. 业务逻辑

- `bitrate_kbps >= bitrate_std` 边界（30000 阈值下正好 30000 kbps 算达标）—— 是否有产品意图？通常「达标」应包含等于
- `sample_rate_passing = True` 当 `audio_stream is None`（无音频流视为达标）—— 当前 `is_passing` 公式 `bitrate_ok and sr_ok` 会让无音频流视频通过 —— 是否符合用户预期？AGENTS.md 已说明规则，但需在审计中确认用户理解
- 阈值改变后已扫描行的 `is_passing` 不重新计算 —— 调高阈值后旧行可能仍显示「达标」错
- `_clear_records` 在扫描中会拒绝但不重置 `scanning` 标志
- 无中止扫描机制 —— 用户无法取消已开始的长扫描

### 6. 资源 / 清理

- `_start_scan` / `_on_file_drop` 中 `self.result_queue = queue.Queue()` 替换旧队列，旧队列若有未消费消息则被 GC —— 一般无害
- 大量扫描（数千行）时每行创建 11 个 `tk.Label` —— 不构成 bug，但属资源问题，不在本审计范围
- `tk.StringVar` 替换时旧实例是否被 GC
- `scanning` 标志在异常路径下未复位

## 严重度分级

| 级别 | 图标 | 定义 |
|---|---|---|
| 致命 | 🔴 | 用户数据丢失、应用无法恢复 |
| 高 | 🟠 | 明显错误结果但有兜底，或可观察到的状态错乱 |
| 中 | 🟡 | 边界条件下行为可疑、不一致，或用户体验差 |
| 低 | 🟢 | 小毛刺、文案、不影响核心功能 |

## 产出物结构

最终 spec（在审计完成后扩展）：

```1. 方法回顾（执行版 vs 设计版差异）
2. 概述（按严重度统计：致命 X / 高 X / 中 X / 低 X）
3. 问题列表（按严重度降序）每条：标题 / 类别 / 位置 file:line / 描述 / 复现 / 建议修复方向（不实施）
4. 已验证为非问题的疑似点（避免下轮再查）
5. 断言脚本执行结果
6. 未覆盖项（GUI 状态、线程时序等只能靠后续测试覆盖）```

## 不在本期范围

- 不修复 bug、不动 build、不动 AGENTS.md / CLAUDE.md
- 不引入测试框架
- 不重构 937 行单文件为多模块

## 改动清单

- 新增：`docs/superpowers/specs/2026-09-16-bug-audit-design.md`（本文件）
- 新增：`docs/superpowers/specs/2026-09-16-bug-audit-assertions.py`（一次性断言脚本）
- 修改：本 spec 在审计完成后追加「概述 / 问题列表 / 断言结果」三节
- 无代码改动