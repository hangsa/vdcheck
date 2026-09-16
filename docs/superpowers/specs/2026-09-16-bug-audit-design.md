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

---

## 审计发现 — 崩溃 + 数据正确性

### 🔴 致命 macOS/Linux 上扫描启动即崩溃，UI 永久卡死
**位置**: `video_checker.py:86-102`（`run_ffprobe`）+ `video_checker.py:699-719`（`_scan_worker`）+ `video_checker.py:912-926`（`_scan_files_worker`）
**类别**: 崩溃
**描述**: `run_ffprobe` 中 `subprocess.STARTUPINFO()`、`subprocess.STARTF_USESHOWWINDOW`、`subprocess.CREATE_NO_WINDOW` 仅 Windows 存在；非 Windows 平台访问这些属性直接抛 `AttributeError`。当前 `except` 列表为 `(subprocess.TimeoutExpired, json.JSONDecodeError, OSError)`，**不含 `AttributeError`**，因此异常会向外传播。更糟的是 `_scan_files_worker` 完全外层无 try/except，线程静默死亡但从未向队列发 `done`，主线程 `_check_queue` 不断轮询，`self.scanning` 永远为 True，扫描按钮永久 disabled。`_scan_worker` 同理（虽然 `scan_video_files` 会先运行，但若目录含至少 1 个视频文件，下一次 `run_ffprobe` 必崩且不发 `done`）。
**复现**: 在 macOS/Linux 上启动 app，输入任意含视频文件的目录，点击「扫描」。`run_ffprobe` 第 86 行立即抛 `AttributeError`，worker 线程死亡；UI 显示「正在扫描」但永远停在那里，按钮变灰无法再点。拖拽文件路径则 100% 复现。
**建议**: 把 `startupinfo=...` / `creationflags=...` 放在 `if sys.platform == 'win32':` 分支里构造并传入；或把 `except` 扩为 `except Exception`，让 `run_ffprobe` 退化为返回 `None`。

### 🟠 高 移动达标文件后，UI 表格与磁盘状态不一致（混乱 + 重复移动）
**位置**: `video_checker.py:764-802`（`_move_passing_files`）+ `video_checker.py:804-842`（`_move_all_files`）
**类别**: 数据正确性
**描述**: 移动成功后，`self.video_results` 列表、`self.grid`（VideoGridView）表格、`status_var` 均不刷新。表格继续显示已不存在的旧路径。再次点击「移动达标文件」时，`_get_unique_dest` 会因为 dest 目录里已有同名文件而追加 `_1`、`_2`…，但源文件已不在 `video_results` 命中的旧路径上（已搬走）—— 重新扫描前用户无法恢复视图，更糟的是用户可能反复点击导致 dest 端出现 `video_1.mp4`、`video_2.mp4` 的链式命名混乱。配合下方 `shutil.move` 部分失败吞错，会让用户看到 `moved=N` 误以为全部成功，实际可能半失败但表格不变 —— 操作后状态机不可信。
**复现**: 扫描 `~/Videos/`、设置阈值全过、目标 `~/Videos/Checked/`、点击移动。观察表格内容未变；立刻再次点击移动，dest 端逐次出现 `video_1.mp4`、`video_2.mp4`，表格里看到的仍是旧路径，新生成的是被搬来的内容还是源里原有的已无法区分。
**建议**: 移动成功后调用 `self.video_results.remove(info)` 与 `self.grid.remove_row(info)`；记录每次失败的 source/dest 让用户能手动恢复；对 `shutil.move` 的部分失败至少提示「部分文件状态未知，请人工核对」。

### 🟠 高 移动时 dest 是 source 的子目录时，反复操作会改写命名（数据可恢复但语义错乱）
**位置**: `video_checker.py:764-802` + `video_checker.py:844-857`（`_get_unique_dest`）+ `video_checker.py:681`（默认 dest）
**类别**: 数据正确性
**描述**: 默认 dest = `os.path.join(directory, "Checked")` 是 source 的子目录路径。首次移动后 dest 端就是 source 的子集；若用户未重扫就再次点「移动达标」，`_get_unique_dest` 会把已搬过去的同名文件 `_N` 化重命名 —— 文件未丢但命名语义被悄悄改写。配合「移动后 UI 不刷新」问题更糟：用户看到的还是旧路径，无法察觉已发生的 rename。
**复现**: 扫描 `~/Videos/`（含 `a.mp4` + 子目录 `Checked/a.mp4`），点击移动达标。`Checked/a.mp4` 不变（首次搬过去的）。**再次**点击移动达标（不重扫）：`Checked/a.mp4` 被改名为 `Checked/a_1.mp4`，`moved = 1`，UI 显示成功。源端那个原本叫 `a.mp4` 的文件已不存在于原位置 —— 用户以为「没移动」实际已被改名。
**建议**: 移动前校验 `dest_dir` 不是 source 的祖先目录，是则拒绝并提示；或每次移动后刷新 `video_results` 与 `grid`，并把已移动行标记/移除。

### 🟠 高 `r_frame_rate="0/0"` 时显示 `"0.00 fps"` 而非 `N/A`
**位置**: `video_checker.py:137-143`
**类别**: 数据正确性
**描述**: Task 3 断言已确认：当 `r_frame_rate = "0/0"` 时，`den = 0` 触发 `else fps = 0`，被格式化为 `"0.00 fps"`。但代码本意在 `den == 0` 时等价于「未知」（ffprobe 在某些容器/编码失败场景确实返回 `"0/0"`），此处把它降级为「0 fps」会让用户误以为帧率确为 0。从任务上下文看属可观察到的错误结果，但 UI 没有「达标/不达标」与 fps 直接挂钩，所以不是数据丢失。
**复现**: 构造 `r_frame_rate = "0/0"` 的 ffprobe JSON 调用 `parse_video_info`。返回 `frame_rate = "0.00 fps"`。
**建议**: 当 `den == 0` 或 `num == 0 && den == 0` 时，`frame_rate = "N/A"`。仅一行改动。

### 🟠 高 `_scan_files_worker` 用 `os.path.dirname(files[0])` 当 base_path，单文件或多根拖拽时 rel_path 失真
**位置**: `video_checker.py:915` + `video_checker.py:204-212`（`parse_video_info`）
**类别**: 数据正确性
**描述**: 拖拽多文件落在不同子目录时，所有 `rel_path` 都基于第一个文件的目录，导致第二个文件的相对路径是 `../sub/` 风格甚至跨出 base 变 `./`；拖拽单个根目录文件（如 macOS 上 `~/Downloads/a.mp4`）时 `os.path.dirname` 返回 `~/Downloads`，但若拖的是 `/` 这种边界路径就退化为 `'.'` 导致 `rel_path` 全是 `./`，多个不同子目录的文件在表格里无法区分。这与检查清单第 2 节一致。
**复现**: 把 `~/Videos/A/a.mp4` 与 `~/Videos/B/b.mp4` 同时拖到窗口，表格里两行的 `rel_path` 都基于 A，会出现 `../B/b.mp4` 这种不直观路径。
**建议**: 拖拽时以所有文件的共同祖先目录作为 `base_path`（`os.path.commonpath`），或干脆把 `rel_path` 退化为绝对路径的 basename 前缀。

### 🟠 高 `shutil.move` 跨设备可能半完成，错误被静默吞掉
**位置**: `video_checker.py:794` + `video_checker.py:834`
**类别**: 数据正确性
**描述**: `shutil.move` 在跨文件系统时会 `copy2` 然后 `os.unlink` 源文件；若 copy 成功但 unlink 失败（权限、AV 扫描器锁定），源端文件仍在，dest 端也有副本 —— 重复文件出现。但更危险的是中途 `copy2` 写一半被中断（磁盘满、用户注销）时，dest 端是损坏文件，源端已被 unlink，**文件彻底丢失且 UI 仅报 `moved += 1`**。
**复现**: 把 dest 设在挂载的网络盘；中途断网；UI 显示「成功移动 N 个文件」，实际 dest 端都是 0 字节占位文件，源端已删。
**建议**: 移动前校验目标剩余空间；移动后 `os.path.getsize(dest) == os.path.getsize(src)` 校验；或换为 `shutil.copy2 + 校验 + os.remove` 两段式。

### 🟠 高 `_get_unique_dest` 在并发或重命名链下会无限加后缀
**位置**: `video_checker.py:844-857`
**类别**: 数据正确性
**描述**: `_get_unique_dest` 假定 dest 目录里 `name.ext`、`name_1.ext`、`name_2.ext` … 之后没有同名文件。但若用户源文件本身就叫 `foo_1.mp4`（拖拽目录里既有 `foo.mp4` 又有 `foo_1.mp4`），dest 端又会再生成 `foo_1_1.mp4`，命名语义丢失；循环多次扫描+移动后会出现 `foo_1_2_3_4.mp4` 这种链式后缀。属可观察到的错误结果（用户命名语义被悄悄破坏），与发现 #2 同级。
**复现**: 源目录有 `a.mp4`、`a_1.mp4`，目标目录已有 `a.mp4`。移动后 dest 端得到 `a_1.mp4`（与源同名冲突的用户语义已被破坏）和 `a_1_1.mp4`（源 `a_1.mp4` 被改名后的产物）。
**建议**: 用 `uuid.uuid4().hex[:8]` 短哈希替代 `_N` 计数器，避免链式后缀；或维持用户命名习惯但加源目录短哈希。

### 🟠 高 `_move_passing_files` / `_move_all_files` 未校验 `dest_dir == source`
**位置**: `video_checker.py:771` + `video_checker.py:811`
**类别**: 数据正确性
**描述**: 如果用户把 dest_var 设为与 source 完全相同的目录（粘贴错了），`shutil.move("a.mp4", "a.mp4")` 在 Windows 上会抛 `OSError` 被捕获；但在 Linux 上 `os.rename` 同源到同源会成功（no-op），文件实际未移动却计入 `moved += 1`，UI 误报成功。属于明显的错误结果（moved 计数 ≠ 实际移动文件数）。
**复现**: Linux 上扫描 `./Videos`，目标设为 `./Videos`，点击移动。`moved = N` 但磁盘零变化。
**建议**: 移动前用 `os.path.realpath` 解析两边绝对路径并比较，相等则拒绝。

### 🟡 中 `parse_video_info` 未捕获 `tag` 不是 dict 的异常
**位置**: `video_checker.py:194-202`
**类别**: 崩溃
**描述**: 假定 `fmt.get('tags', {})` 返回 dict；部分 ffprobe 输出在 `tags` 为字符串或缺失时仍返回 `{}`，但理论上若 ffprobe JSON 损坏或被中间层修改（用户手改 JSON 后用 `subprocess.run` 喂入不可能，但 hook/拦截器可能），`tags` 可能为 list 或其他类型。`for k, v in tags.items():` 会抛 `AttributeError`。虽然当前 main path 不会触发，但 `parse_video_info` 标注为「可独立测试」，断言脚本里手写 fixture 时若不注意就会爆。
**复现**: 构造 `fmt = {"tags": ["title", "foo"]}` 调 `parse_video_info`，抛 `AttributeError`。
**建议**: `tags = fmt.get('tags') or {}` 之后再 `if isinstance(tags, dict)` 守卫；或在 except 中退化为空 dict。

### 🟡 中 `_on_file_drop` 解析 Windows `{path}` 格式失败时把整串当路径
**位置**: `video_checker.py:882-887`
**类别**: 数据正确性
**描述**: macOS/Linux 拖拽格式是 `file:///path/with%20space/file.mp4` 或裸路径；`re.findall(r'\{([^}]+)\}', ...)` 拿不到就 fallback 到 `file_list = [files]`，把整串 `"file:///a.mp4 file:///b.mp4"`（含分隔空格）当成单个文件路径交给后续流程。最终 `os.path.splitext` 拿到 `.mp4` 通过扩展名检查，再去 `open("file:///a.mp4 file:///b.mp4")` 失败 → ffprobe 报文件不存在 → 返回 `None` → 整批文件无声丢失。
**复现**: macOS 上把两个 .mp4 一起拖进窗口。表格为空，无错误提示，用户以为「不支持拖拽」。
**建议**: 把 `re.findall(r'\S+', files)` 作为通用兜底，或先剥 `file://` 头再 split。

### 🟡 中 `scan_video_files` 非递归路径静默吞 `OSError`，空结果不可区分于权限错误
**位置**: `video_checker.py:524-531`
**类别**: 数据正确性
**描述**: 非递归分支用 `try: os.listdir(...) except OSError: pass` 静默吞掉所有错误。用户面对的「找不到视频」既可能是「真的没有」，也可能是「目录无权限/路径损坏/被其他进程锁定」，但 UI 上完全无差别。批量扫描含权限异常目录时排查困难。
**复现**: 把目录权限设为 `chmod 000 ~/private/` 后用非递归扫描；表格为空，无错误提示。
**建议**: 捕获 `OSError` 后向 `result_queue` 发 `('status', f'目录读取失败: {e}')`，让用户在状态栏看到原因。

### 🟡 中 大目录扫描（数千文件）时，每行 11 个 Label + 主线程 `_check_queue` 100ms 节奏会被队列堵
**位置**: `video_checker.py:721-737`
**类别**: 崩溃（潜在）/ 数据正确性
**描述**: worker 线程以 `result_queue.put` 单条发送 `_add_result`；主线程 `get_nowait` 循环一次只处理 1 条就被 `queue.Empty` 打断回到 `after(100, ...)`。当 worker 高速产生结果时，UI 实际刷新节奏 = 100ms × 队列积压量。表格里每行 11 个 Label + Canvas redraw，30+ 行后主线程渲染耗时可能 > 100ms 一次，积压越来越深，最终队列无界增长，内存 OOM。属于崩溃衍生（线程不退出）与性能混合，但首因是缺批量/节流。
**复现**: 在含 5000 个短视频的目录上扫描并设阈值让 90% 通过。约 30 秒后 Python 进程占用 > 1GB RAM。
**建议**（仅指方向，不属本审计修复范围）: 增量渲染 + coalesce，或用 `DoubleVar`/`Text` 缓存行。

### 🟢 低 `os.walk` 默认不跟随 symlink —— 大目录扫不全
**位置**: `video_checker.py:519-523`
**类别**: 数据正确性
**描述**: `os.walk(directory)` 默认 `followlinks=False`，符号链接的视频目录树不会被扫到。Windows 上常见 `My Videos` 是符号链接 / junction；macOS 上 `~/Library/Mobile Documents/com~apple~CloudDocs/` 是云盘符号链接。属于「默认安全选择」（避免循环），但用户察觉不到为何文件少了。
**复现**: 在 macOS 上扫描含一个符号链接到外置硬盘的目录，外置硬盘视频不出现，无提示。
**建议**: 在「递归扫描」复选框旁加副标题「不包含符号链接」；或加 `follow_symlinks` 复选框。

### 🟢 低 `parse_video_info` 在 `relpath` 抛 `ValueError` 时降级为绝对路径
**位置**: `video_checker.py:205-212`
**类别**: 数据正确性
**描述**: `os.path.relpath` 在 Windows 上若 base_path 和 full_path 跨盘符会抛 `ValueError`，已被 except 捕获降级 `rel_path = full_path`。但若 `full_path` 是 UNC 路径（`\\server\share\...`），降级后表格列里出现完整网络路径，非常长且无视觉提示。在文件多时常被截断显示。
**复现**: Windows 上扫描 `\\NAS\Videos\a.mp4`，表格第一列显示完整 UNC 路径。
**建议**: 降级时再用 `os.path.basename` + 父目录缩写。

### 🟢 低 `run_ffprobe` timeout 30s 偏短，4K 大文件首次解析可能超时
**位置**: `video_checker.py:100`
**类别**: 数据正确性
**描述**: 部分大文件（>10GB）或 NAS 慢盘上 ffprobe 启动 + 读取 moov atom 需 > 30s，触 `TimeoutExpired` 被静默吞掉返回 `None`，用户看不到错误，且 UI 显示「正在检测 ...」长时间无变化无超时提示。
**复现**: 在慢速 NAS 上扫描 4K HDR 文件。
**建议**: 把 timeout 暴露为配置；或捕获后向队列发 `('status', f'ffprobe 超时: {name}')` 而非吞掉。