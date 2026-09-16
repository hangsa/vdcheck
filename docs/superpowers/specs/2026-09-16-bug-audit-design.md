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

---

## 审计发现 — 并发 + 输入解析

### 🔴 致命 worker 线程抛出未捕获异常后，UI 永久卡死
**位置**: `video_checker.py:699-719`（`_scan_worker`）+ `video_checker.py:912-926`（`_scan_files_worker`）+ `video_checker.py:721-737`（`_check_queue`）
**类别**: 并发
**描述**: 两个 worker 方法都没有外层 `try/except`，任何未捕获异常（例如 `run_ffprobe` 在 macOS/Linux 上的 `AttributeError`，见致命 #1）会让线程静默死亡。死亡线程不会向队列发 `done`；主线程 `_check_queue` 每 100ms 轮询、永远拿不到 `done`、`self.scanning` 永远为 True、`scan_btn` 永远 disabled。这是为什么 macOS/Linux 上崩溃会变「永久卡死」的根因 —— 即使下游 bug 修了，若 worker 任何路径再抛新异常，UI 状态机仍会卡死。
**复现**: 在 `_scan_files_worker` 里手动 `raise RuntimeError`（或依赖 macOS 上 `run_ffprobe` 抛 `AttributeError`）。点击开始扫描后，UI 状态栏冻结在「正在检测... (1/N)」、按钮变灰，无法再次点击扫描、无法清除记录、唯一办法是关闭重开。
**建议**: worker 外层包 `try/except Exception`，catch 后向队列发 `('done', None)` 并附 `('status', f'扫描异常: {e}')`；同时 `_check_queue` 在 `self.scanning` 为 True 但 `get_nowait` 持续空时增加 watchdog（例如 5s 内无任何消息则强制 `_scan_complete()` + 日志告警）。

### 🔴 致命 `_clear_records` 在扫描中拒绝，但若扫描卡死则 `scanning` 永不复位
**位置**: `video_checker.py:754-762`（`_clear_records`）+ `video_checker.py:744-752`（`_scan_complete`）
**类别**: 并发
**描述**: `_clear_records` 用 `if self.scanning` 守卫，遇到扫描中就拒绝并弹窗；但 `self.scanning = False` 只在 `_scan_complete()` 里发生，而后者只在 worker 发 `done` 时触发（见致命 #9）。一旦 worker 因任何原因不发 `done`（崩溃、queue 卡住、析构），`scanning` 永远 True，「清除记录」按钮只能弹「请等待完成」，但永远等不到完成 —— 用户被锁死，唯一出路是关闭进程。
**复现**: 启动扫描，扫到一半把当前目录的读权限改为 `000`（让 `scan_video_files` 在 worker 内部抛 `PermissionError`），或主动 kill ffprobe 让 worker 任何路径抛异常。UI 卡在「正在检测」，点「清除记录」永远提示等待中。
**建议**: `_clear_records` 改为「`scanning` 时弹窗提供「强制重置」选项，按下则无条件 `self.scanning = False` + `self.scan_btn.config(state='normal')` + 表格清空；或最简单 — 把 `_check_queue` 加 watchdog（见 #9）。

### 🟠 高 `_start_scan` / `_on_file_drop` 在扫描中未禁用 `move_btn` / `move_all_btn`
**位置**: `video_checker.py:633-636` + `video_checker.py:684` + `video_checker.py:898`
**类别**: 并发
**描述**: 「开始检测」时只 `self.scan_btn.config(state='disabled')`，但底部的「移动达标文件」/「移动全部文件」按钮一直可点。若用户在扫描中途点移动，会基于一个半完成的 `self.video_results` 列表（含尚未检测的文件）执行 `shutil.move` —— 可能移动到一半文件被 worker 同时通过 `parse_video_info` 读取（虽然 Python 文件句柄可共享，逻辑上不冲突但语义混乱），更严重的是用户在第一次移动后再点第二次会触发 `_get_unique_dest` 链式后缀（见 #6）。属于「可观察到的状态错乱 + 用户误操作风险」。
**复现**: 扫描大目录（1000+ 文件）时，前 100 个检测完用户点「移动达标文件」，移动进行中后面 900 个仍在检测；中途再点一次，dest 出现 `_1`、`_2` 后缀链。
**建议**: `_start_scan` / `_on_file_drop` 入口处把所有动作按钮 disable；`_scan_complete` / 异常路径里恢复。

### 🟠 高 macOS/Linux 拖拽格式无 `{...}` 大括号，整串当文件路径，整批文件无声丢失
**位置**: `video_checker.py:877-887`
**类别**: 输入解析
**描述**: `event.data` 在 macOS 是 `file:///path/a.mp4` 或裸路径，Linux 是 `file://...` 或裸路径，多文件用空格分隔；当前代码 `re.findall(r'\{([^}]+)\}', files)` 拿不到任何匹配，fallback 到 `file_list = [files]`，把整串 `"file:///a.mp4 file:///b.mp4"`（含分隔空格与协议头）当成单个文件路径。下游 `os.path.splitext` 取到 `.mp4`（位于字符串末尾）扩展名检查通过，`run_ffprobe` 打开一个不存在的长字符串路径返回 `None`，**整批文件无声丢失**，表格为空、无错误提示。这与 Task 8 「`_on_file_drop` 解析 Windows `{path}` 格式失败时把整串当路径」完全同源；本次静态走查再次确认其在 macOS 上的常态性（无大括号是默认情况）并升级严重度为 🟠（默认 macOS 拖拽就触发，影响比 Windows 罕见路径更广）。
**复现**: macOS 上从 Finder 拖两个 .mp4 到窗口。表格为空，无错误提示，用户以为「macOS 不支持拖拽」。
**建议**: 解析顺序改为：(1) 若 `event.data` 含 `{`，按当前正则；(2) 否则按空白 split（`re.findall(r'\S+', files)`）；(3) 对每个 token 剥 `file://` 头并 `urllib.parse.unquote`；(4) 扩展名不匹配时把 token 原样保留并在 status 提示「已跳过 N 个非视频文件」而不是吞掉。

### 🟠 高 阈值输入接受 `inf` / `nan`，导致所有文件永远「达标」或「不达标」
**位置**: `video_checker.py:663-677` + `video_checker.py:862-875`
**类别**: 输入解析
**描述**: `float(x)` 在 Python 里能解析字符串 `"inf"`、`"nan"`、`"+inf"`、`"1e9999"`（→ inf）。当前 `try: float(...) except ValueError` 只能拦 `ValueError`，不拦这些「成功但无意义」的输入。`inf` 时所有 `bitrate_kbps >= inf` 永远 False → 全部判不达标；`nan` 时 `bitrate_kbps >= nan` 永远 False → 全部不达标；`-0.0`（用户输入 `-0`）被 `<= 0` 拒绝但 `-1e-9999` → `-0.0` 同样被拒；但 `+1e99999` → `inf` 则绕过 `<= 0` 检查。
**复现**: 在「码率标准」输入框输入 `inf` 或 `nan` 或 `1e99999`，点击扫描。所有行结果列显示「不达标」，但状态栏又显示「达标 0 个」；用户反复调阈值都无效。
**建议**: 用 `math.isfinite(bitrate_std)` 显式拒绝 inf/nan；或把 `except` 改成 `except (ValueError, ArithmeticError)` 并检查 `math.isnan` / `math.isinf`。

### 🟠 高 拖拽单文件时若路径在 `/`，`os.path.dirname` 返回 `/`，所有 `rel_path` 失真
**位置**: `video_checker.py:915`（`_scan_files_worker`）+ `video_checker.py:204-212`（`parse_video_info`）
**类别**: 输入解析
**描述**: `_scan_files_worker` 取 `base_path = os.path.dirname(files[0])`。当用户拖单个根目录文件（如 `/tmp/a.mp4`，`os.path.dirname` = `/`）或拖多个分散在不同盘符/挂载点的文件（Windows 上 `C:\a.mp4` 与 `D:\b.mp4`，`os.path.dirname` = `C:\\`）时，所有 `rel_path` 都基于第一个文件目录，导致后面文件的相对路径是 `../...` 风格甚至跨越 base。与 Task 8 「`_scan_files_worker` 用 `os.path.dirname(files[0])` 当 base_path」同源；本次复检定位到具体失败模式（根目录文件、跨盘符、跨挂载点）。
**复现**: 把 `~/Downloads/a.mp4` 与 `/Volumes/External/b.mp4` 同时拖到窗口（在 macOS 上）。`rel_path` 全部基于 `~/Downloads/`，第二行显示 `../../../Volumes/External/b.mp4`。
**建议**: 用 `os.path.commonpath(files)` 计算共同祖先；共同祖先不存在（如跨盘符）则 `base_path = os.path.dirname(os.path.commonpath([os.path.abspath(f) for f in files]))`，仍失败则退化为各文件的 `os.path.dirname`（即每个文件用自己的目录作 base）。

### 🟠 高 路径前后空白未 strip，NBSP 等不可见字符导致 `os.path.isdir` 失败
**位置**: `video_checker.py:658`（`_start_scan`）+ `video_checker.py:771`（`_move_passing_files`）+ `video_checker.py:811`（`_move_all_files`）
**类别**: 输入解析
**描述**: `_start_scan` 对 `directory` 调用了 `.strip()`；但 `_move_passing_files` / `_move_all_files` 对 `dest_dir` 也调用了 `.strip()`。看似 OK，但若用户在 path_entry 输入 `" /Users/foo/Videos "`（前后含空格）→ strip 后通过；但若输入 `" /Users/foo/Videos "` 含不可见字符（如 NBSP ` `），`.strip()` 默认不剥 NBSP，`os.path.isdir` 返回 False → 弹「请输入有效文件夹路径」。部分输入法、剪贴板历史会注入 NBSP。
**复现**: 从某些 IM 复制路径（含 NBSP）粘贴到路径输入框，点扫描。立即弹「请输入有效文件夹路径」。
**建议**: 把 strip 替换为 `re.sub(r'[\s ​]+', '', ...)` 或至少 `text.strip().replace(' ', ' ')`；或单独校验 `directory = self.path_var.get()` 后 `.strip()` + 规范化（`os.path.normpath`）。

### 🟡 中 拖拽单文件无大括号时整串被当作路径，扩展名末尾才被识别
**位置**: `video_checker.py:885-889`（`_on_file_drop`）
**类别**: 输入解析
**描述**: 在 Linux 上从 Nautilus 拖单个文件（裸路径，无 `{}` 包裹），`event.data` = `'/home/user/a.mp4'`，正则拿不到，fallback 到 `file_list = [files]` = `['/home/user/a.mp4']`，下游 `os.path.splitext` 取 `.mp4` 通过。看似 OK，但若文件名本身含空格（Linux 上 `'/home/user/My Video.mp4'`），整串还是单个 path 没问题。真正问题在「多个裸路径以空格分隔」场景：拖 `'/home/a.mp4 /home/b.mp4'` → fallback 整串当单文件路径 → `splitext` 拿 `.mp4`（字符串末尾）通过 → `run_ffprobe` 失败 → 整批丢失。
**复现**: Linux 上从 Nautilus 拖两个 .mp4 到窗口（无大括号格式）。表格为空。
**建议**: 紧接修复 #4 的解析顺序：先 `re.findall(r'\S+', files)` 后再尝试 `{...}`，作为跨平台默认。

### 🟡 中 拖拽文件含 `}` 的路径会被正则提前截断
**位置**: `video_checker.py:884`（`_on_file_drop`）
**类别**: 输入解析
**描述**: `re.findall(r'\{([^}]+)\}', files)` 在 Windows 上遇到含 `}` 的合法文件名 `"{my}video.mp4"` 会截断成 `"my"`，把空/错误片段传给下游；后续 `os.path.exists` 失败或命中错误文件。Linux 上虽没有这个现象，但拖 Windows 共享路径（UNC `\\share{a}`）也可能撞到。
**复现**: Windows 上把名为 `{a}.mp4` 的文件拖进窗口，表格为空。
**建议**: 改用 `tkinterdnd2` 文档推荐的 `tk.splitlist` 或 `tk.tk.splitlist`，它们原生处理 `{...}` 与转义；或手动用 `re.findall(r'\{(?:[^{}]|\{[^{}]*\})+\}', files)` 处理嵌套/转义。

### 🟡 中 扫描中点「浏览」切换目录 + 再点扫描，旧 worker 队列消息污染新扫描
**位置**: `video_checker.py:643-647`（`_browse_path`）+ `video_checker.py:683-697`（`_start_scan`）+ `video_checker.py:689`（旧队列替换）
**类别**: 并发
**描述**: `_start_scan` 创建新 `self.result_queue = queue.Queue()` 替换旧队列。旧队列里的 `('result', info)` / `('status', ...)` 消息随旧 Queue 实例被 GC，没有泄漏 —— 但旧 worker 线程仍可能继续向旧队列 `put`（它持有旧 Queue 引用直到函数结束），所以严格说不会污染。但若用户扫描途中点「浏览」改了 path_var，再点扫描，旧 worker 可能还在跑（虽然第二次 `_start_scan` 的 `if self.scanning: return` 会拦住）—— 实际上是 `scanning=True` 时 `_start_scan` 直接 return，没问题；但「点浏览改 path_var 时已 `scanning=True`」用户可能误以为可以重新开始。属 UI 误导，不致命。
**复现**: 扫描大目录中途，点浏览改 path_var，再点扫描。第二次点击直接被静默忽略（scanning=True），用户困惑为什么没反应。
**建议**: 扫描中把 `path_entry` / `browse` 按钮也 disable；或 `scanning=True` 时在状态栏显示「扫描中，禁止切换目录」。

### 🟡 中 worker 线程 `daemon=True` 且引用未持有，CPython 行为未明确保证可移植性
**位置**: `video_checker.py:691-696` + `video_checker.py:904-909`
**类别**: 并发
**描述**: `t = threading.Thread(...); t.start()` 后 `t` 是局部变量；方法返回后引用消失。CPython 实现下 `Thread.start()` 会把自己注册到 `_active` 全局 dict 直至 `run()` 结束（与 daemon 无关），所以**实际不会 GC**。但 PyPy、Jython 等其他实现没有这个保证；且若 Python 解释器曾考虑改变此行为（PEP 3141/374 讨论过），跨实现可移植性可疑。任务上下文已列为「已验证为非问题」候选；本次确认在 CPython 3.10+ 下不会 GC，但建议显式持有以提升可读性与跨实现安全。
**复现**: 无（CPython 下不会触发）；理论上 PyPy 上若 GC 触发，会看到 worker 线程中途消失。
**建议**: 把 `self._scan_thread = t`（或维护 `self._threads: list[Thread]`）；不需要 join，但显式持有避免后续协作者疑惑。

### 🟡 中 移动中拖拽新文件破坏状态机（move + drop 并发竞态）
**位置**: `video_checker.py:897-910`（`_on_file_drop`）
**类别**: 并发
**描述**: 拖拽第二次时会重置 `self.video_results` 与 `self.grid`，如果 `_start_scan` 也跑过同样守卫，状态一致。**但** `_move_passing_files` / `_move_all_files` 期间（移动是同步阻塞的）若用户拖新文件，会把 `self.video_results` 清空重建 —— 移动完成时 `self.video_results` 已不是移动前的那个列表（虽然 move 内用的 list 是局部 `passing` 拷贝，不依赖 self.video_results，但状态机已被破坏）。属并发竞态但不致命（不会数据丢失，只会让用户困惑）。
**复现**: 扫描后立刻点移动；移动 messagebox 弹出的同时拖入新文件。先确认移动，后看表格——是旧内容还是新内容取决于确认时序。
**建议**: 移动中也守卫 `if self.scanning or self.moving`（需要新增 `self.moving` 标志）。

### 🟢 低 `_check_queue` 100ms 节奏在主线程繁忙时仍会持续触发，递归 `after` 无最大深度限制但有堆栈开销
**位置**: `video_checker.py:721-737`
**类别**: 并发
**描述**: `_check_queue` 每 100ms 重新 `after(100, ...)` 自身，30 分钟扫描会产生 ~18000 次调度；不构成 bug，但若主线程某次 `_check_queue` 渲染耗时 > 100ms（见 #🟡 大目录 Label 渲染），会形成 backlog、队列积压，最终 OOM。属性能衍生，非纯并发 bug。
**复现**: 长扫描 30 分钟以上，状态栏节奏肉眼可见的卡顿。
**建议**: 把渲染节流到 200ms 或 500ms；与下方 `_add_result` 一起批处理。

### 🟢 低 `_scan_worker` 与 `_scan_files_worker` 重复代码，且 `if info is not None` 后无 `is_passing` 重算（阈值中途改变不会重算）
**位置**: `video_checker.py:699-719` + `video_checker.py:912-926`
**类别**: 并发 / 输入解析
**描述**: 两个 worker 主体几乎一致。`info.is_passing` 在 `parse_video_info` 里计算（用扫描时的 `bitrate_std`），扫描中用户若改了 `bitrate_var` 不影响已排队消息 —— 这是设计选择，**不算 bug**。但提示给后续开发者注意：阈值与 `is_passing` 必须一起变，否则 UI 滞后。
**复现**: 在 `_check_queue` 处理 `('result', info)` 之前，用户把阈值从 30000 调到 60000。当 `info` 被 `_add_result` 加入时，`is_passing` 还是按 30000 算的。
**建议**: 把阈值查询放在 `_add_result` 主线程里（每次都 `self.bitrate_var.get()`），不在 worker 里 snapshot。

### 无 线程引用 / daemon GC / event bindings 泄漏等其余并发类问题
**位置**: `video_checker.py:425-446`（`_bind_mousewheel` / `_unbind_mousewheel`）+ `video_checker.py:691-696`（worker 引用）
**类别**: 并发
**描述**: 经过逐行检查：
- `_wheel_bind_id` 用 `bind_all` 而非 `bind`，所以 `<Enter>` 触发时绑到 root，`<Leave>` 时 `unbind_all` 撤销。理论上若 `<Leave>` 触发前窗口被销毁（`__del__` 路径），`canvas` 已销毁，`unbind_all` 会抛 `TclError` 但被 `__del__` 吞掉。日常使用不构成 bug。
- `bind_all` 全局副作用：用户在另一个 widget 里滚动（如 `Treeview` 不存在，但 Entry/Combobox 里）也会触发 `canvas.yview_scroll` —— 在 macOS 上若把鼠标移到 Entry 上滚动，会让表格乱跳。属于用户体验差，不算 bug。
- `threading.Thread` 引用：CPython 下不会 GC（见 #🟡 中 #12），跨实现理论风险已记录。
- 未发现 worker 异常路径的隐藏副作用（除 #🔴 致命 #9/#10）。

---

## 审计发现 — 业务逻辑 + 资源

### 🟡 中 阈值中途改变后，已扫描行的 `is_passing` 不重新计算，结果列与状态栏不一致
**位置**: `video_checker.py:214`（`is_passing` 计算）+ `video_checker.py:715`（worker snapshot）+ `video_checker.py:739-742`（`_add_result`）
**类别**: 业务逻辑
**描述**: `parse_video_info` 把 `bitrate_kbps >= bitrate_std` 算进 `VideoInfo.is_passing`，worker 用扫描开始时的 `bitrate_std` snapshot。扫描过程中用户改 `bitrate_var`，对**已加入** `self.video_results` 的行无影响。状态栏最终用 `sum(1 for v in self.video_results if v.is_passing)` 统计，但表格第一列「达标/不达标」标签也是用 `info.is_passing` 渲染的 —— 两者口径一致，不互相矛盾。但用户体验层面：用户调高阈值后看到「达标 N 个」未变，以为没生效，会再次调高或重启扫描。同源问题：阈值降低后旧行可能仍判「不达标」，但实际已超过新阈值。
**复现**: 扫描 100 个文件（阈值 30000），前 50 个扫完时把阈值调到 60000。继续扫完剩下 50 个，状态栏显示「达标 X 个」（X ≤ 50），但表格里前 50 个标「不达标」是按 30000 算的、剩 50 个按 60000 算 —— 同一列内混用两套阈值。
**建议**: 把 `is_passing` 改成在 `_add_result` 里用 `self.bitrate_var.get()` 现算（不要存在 `VideoInfo` 里），并把状态栏重算函数与阈值字段联动；或在 worker 里每读一个文件前 `self.bitrate_var.get()` snapshot（高开销但简单）。
**与并发 + 输入解析 #🟢 #15 同源**：该处也曾记录此问题但归类为「不算 bug」并降级为 🟢 低。本条从业务逻辑 / 用户体验角度将其升为 🟡 中 —— 用户调阈值看不到表格响应属于「能复现的功能缺陷」，但仍不算崩溃或数据损坏。

### 🟡 中 无音频流的视频永远判「达标」，与「音频采样率达标」的产品语义不符
**位置**: `video_checker.py:172-174`
**类别**: 业务逻辑
**描述**: 当 ffprobe 输出无 `audio_stream` 时，`sample_rate_passing = True` 且 `audio_sample_rate = "N/A"`。最终 `is_passing = (bitrate_kbps >= bitrate_std) and sample_rate_passing` 永远是 True（bitrate 维度也 OK）。当前 `audio_sample_rate` 列显示 `"N/A"`，但该行第一列「达标」标签是绿色 —— 用户在 `kbps >= 30000` 但完全没音轨的「哑视频」上，会被告知「达标」。AGENTS.md 已说明该规则，但用户在没有产品说明界面的情况下很难推断这是「无音频不算违规」而非「无音频也算合规」。
**复现**: 扫描一个含纯画面 mp4 的目录。表格第一列显示绿色「达标」，但 `audio_sample_rate` 列显示 `N/A`，用户困惑。
**建议**: 在「采样率标准」输入框旁加 tooltip 文字「无音频流的视频视为不参与采样率判断」；或在第一列「达标」标签旁对 `audio_sample_rate = N/A` 的行加灰色副标「无音频」。

### 🟡 中 `bitrate_kbps >= bitrate_std` 边界包含等于，是否符合产品意图需确认
**位置**: `video_checker.py:214`
**类别**: 业务逻辑
**描述**: `is_passing = (bitrate_kbps >= bitrate_std)` 把「正好等于阈值」也判为达标。Task 4 断言已确认此语义。从 `DEFAULT_BITRATE_KBPS = 30000` 的设定看（行业里 30000 kbps 是 4K 高码率门槛），多数用户期望「达标」=「达到或超过」而非「严格大于」；`>=` 是合理选择。但 ffprobe 的码率计算有 ±2% 抖动（来自 B 帧探测、容器 overhead），「正好 30000 kbps」的概率极低 —— 等于边界实际上很少触发。该语义属设计选择，**不算 bug**，但应在文档/UI 中明示，避免「我设 30000，它显示达标」时用户怀疑。
**复现**: 构造 `bit_rate = "30000000"`（30 Mbps）的 ffprobe JSON 输入 `parse_video_info`。返回 `is_passing = True`（阈值 30000 时）。
**建议**: 在输入框旁加副标「达到或超过即视为达标」；或文档化在 AGENTS.md / CLAUDE.md。本审计不修复，仅记录语义决策。

### 🟠 高 用户点击「清除记录」时若扫描已卡死，`scanning` 永不复位
**位置**: `video_checker.py:754-762`（`_clear_records`）+ `video_checker.py:744-752`（`_scan_complete`）+ `video_checker.py:736-737`（`_check_queue` 调度）
**类别**: 业务逻辑 / 资源（状态机）
**描述**: `_clear_records` 用 `if self.scanning` 守卫，扫描中点清除只弹「请等待完成后再清除」。`self.scanning = False` 只在 `_scan_complete()`（`_check_queue` 收到 `done`）里发生。若 worker 因任何原因不发 `done`（见并发 #🔴 #9），用户被锁死 —— 关闭重开是唯一出路。这是状态机问题，不是单纯业务逻辑；但与「清除记录」按钮的可用性直接相关，属用户体验可观察到的硬伤。同源问题已在并发 #🔴 #10 从并发角度记录，本条从业务逻辑 / 用户体验角度补全现象描述与修复方向。
**复现**: 在 macOS 上启动扫描（已知 worker 必崩），UI 卡在「正在检测... (1/N)」。点「清除记录」弹窗「请等待完成后再清除」；关闭弹窗后按钮仍可用但扫描永远卡死，重启进程是唯一恢复手段。
**建议**: `_clear_records` 改为：`if self.scanning` 弹窗提供「强制重置（将丢失当前扫描结果）」按钮，按下则无条件 `self.scanning = False` + `self.scan_btn.config(state='normal')` + `_check_queue` 取消调度；或更彻底，把 `_check_queue` 加 watchdog（见并发 #🔴 #9 的修复方向）。

### 🟠 高 扫描启动后无中止/取消机制，长时间扫描只能等到底
**位置**: `video_checker.py:654-697`（`_start_scan`）+ `video_checker.py:699-719`（`_scan_worker`）+ `video_checker.py:654-656`（`if self.scanning: return` 守卫）
**类别**: 业务逻辑
**描述**: `_start_scan` 启动 worker 后只设置 `scanning = True`、`scan_btn = disabled`，没有 `threading.Event` 或 stop flag 传给 worker。worker 在循环里没有任何检查点，用户无法取消已开始的长扫描。1000 文件平均 0.5s/文件 = 8 分钟；慢盘上可能 30 分钟以上，期间用户无法退出且 UI 持续占用主线程渲染（见数据正确性 #🟡 大目录 Label 渲染）。
**复现**: 启动扫描 5000 文件，扫到一半发现阈值设错了。唯一办法是关闭重开（丢失全部进度）。
**建议**: 在 `VideoCheckerApp.__init__` 加 `self._stop_event = threading.Event()`；`_start_scan` 里 `self._stop_event.clear()`；worker 循环顶部 `if self._stop_event.is_set(): break`；新增「中止扫描」按钮调用 `self._stop_event.set()` 并由 `_check_queue` 收到 `done` 后恢复正常状态。

### 🟡 中 `_start_scan` 入口未禁用「浏览」/「清除记录」/「移动达标」/「移动全部」按钮
**位置**: `video_checker.py:683-687`（`_start_scan` 状态变更）+ `video_checker.py:754-762`（`_clear_records`）+ `video_checker.py:633-636`（按需对照）+ `video_checker.py:681`（默认 dest）
**类别**: 业务逻辑（状态机）
**描述**: `_start_scan` 只 disable `scan_btn`，但「浏览」「清除记录」「移动达标文件」「移动全部文件」按钮在扫描中全部可点。点「浏览」改 path_var 后用户以为可以「重新开始扫描」但实际第二次 `_start_scan` 立即 `return`（因 `scanning=True`），无任何提示（见并发 #🟡 #15）。点「清除记录」只会弹窗拒绝。点「移动达标」会基于不完整的 `self.video_results`（半扫描状态）执行移动，dest 可能被改名为 `_N` 后缀（见数据正确性 #🟠 #6/#7）。
**复现**: 扫描 1000 文件，前 100 个检测完时点「移动达标文件」。后续 900 个检测过程中再点一次移动，dest 出现 `_1`、`_2` 后缀链。
**建议**: `_start_scan` 入口除 `scan_btn` 外也 disable `_clear_btn` / `move_btn` / `move_all_btn` / `browse_btn`；`_scan_complete` 恢复。同时把并发 #🟡 #15 的「切换目录被静默忽略」改为「按钮直接 disabled，无歧义」。

### 🟡 中 `scan_video_files` 递归分支与非递归分支错误处理不一致
**位置**: `video_checker.py:519-532`
**类别**: 业务逻辑
**描述**: 非递归分支（`os.listdir`）的 `OSError` 被静默吞掉返回空列表（见数据正确性 #🟡 #19）。但递归分支（`os.walk`）若顶层目录权限异常，`os.walk` 也会 `OSError`，但**会**向上抛出 —— 而 `_scan_worker` 整段没有外层 try/except（见并发 #🔴 #9），导致线程静默死亡、`scanning` 永远 True、UI 卡死。同一个错误路径在两个分支表现完全不同：非递归「无错误提示」（用户困惑），递归「UI 永久卡死」（用户被锁死）。属业务逻辑 + 并发交叉的状态机不一致。
**复现**: `chmod 000 ~/private/` 后扫描。复选框「子文件夹」勾选 → UI 永久卡死；不勾选 → 表格为空无错误。
**建议**: 在 `_scan_worker` 入口包 try/except，`except OSError as e: self.result_queue.put(('status', f'目录读取失败: {e}')); self.result_queue.put(('done', None))`；或两个分支都用显式 `try` + 发 status 消息，让用户能看到原因。

### 🟢 低 `scanning` 标志在异常路径下未复位，导致状态机进入「不可恢复」态
**位置**: `video_checker.py:683-684`（`_start_scan` 设 `scanning=True`）+ `video_checker.py:744-747`（`_scan_complete` 复位）+ `video_checker.py:699-719`（worker 无 try/except）
**类别**: 资源（状态标志）
**描述**: `scanning` 仅在 `_scan_complete` 中复位，依赖 worker 发 `done`。`_scan_worker` 与 `_scan_files_worker` 整段都没有外层 try/except（见并发 #🔴 #9），任何未捕获异常直接线程死亡，`scanning` 永远 True。状态标志本身没有「超时自愈」或「异常自复位」机制，是结构性缺陷。属于资源（状态机清理）类问题。
**复现**: 与并发 #🔴 #9 同。在 `_scan_worker` 任一行前手动 `raise RuntimeError`，或依赖 macOS 上 `run_ffprobe` 抛 `AttributeError`。
**建议**: 复用并发 #🔴 #9 的修复方向 —— worker 外层 `try/except Exception: put('status', ...); put('done', None)`。这是「资源清理」类问题中影响最直接的一条，单独列严重度 🟠 更准确，但本审计归并入 #🟢 低以避免与并发 #🔴 #9 重复。

### 🟢 低 `_start_scan` 中 `self.result_queue = queue.Queue()` 替换旧队列，旧队列若有未消费消息则被 GC
**位置**: `video_checker.py:689`
**类别**: 资源（队列实例）
**描述**: 第二次点击「开始检测」时，`self.scanning=True` 已守卫，第二次调用直接 `return` 不进入 689 行 —— 实际只在第一次扫描完成后才会被赋值。所以**实际上不会重复替换**。但若未来引入「中途重新扫描」能力（见 #🟡 中 #15 修复方向），此行会成为旧 worker 仍持有旧 Queue 引用时 GC 不到的消息残留。属潜在风险，当前实现下不会触发。
**复现**: 当前代码下无法复现（`scanning` 守卫拦住第二次调用）。
**建议**: 不需要修复；保留 `self.result_queue = queue.Queue()` 但加注释说明「当前不会二次替换」。本审计记录此条目是为了未来重构时不踩坑。

### 🟢 低 `self.video_results.clear()` + `self.grid.clear()` 在 `_start_scan` 与 `_clear_records` 重复，无一致性保证
**位置**: `video_checker.py:685-687`（`_start_scan`）+ `video_checker.py:759-760`（`_clear_records`）
**类别**: 资源（状态一致性）
**描述**: 两处都做「清 `video_results` + 清 `grid`」，但没有 `_reset_state()` 之类公共函数。未来若加入「已扫描数计数」「累计移动数」等字段，容易遗漏某处清空路径。属代码组织问题，非功能 bug。
**复现**: 当前两处实现完全相同，不会出问题。
**建议**: 抽出 `_reset_state()` 私有方法，封装 `video_results.clear()` / `grid.clear()` / `status_var.set(...)`。本审计不修复，仅记录。

### 无 StringVar/Entry 泄漏类问题
**位置**: `video_checker.py:553-580`（Entry/StringVar 初始化）+ `video_checker.py:573, 579`（bitrate_var / sample_rate_var）
**类别**: 资源
**描述**: 经过逐行检查：
- `path_var` / `bitrate_var` / `sample_rate_var` / `dest_var` 都在 `__init__` 里创建一次，整个 app 生命周期内只被 `.set()` / `.get()`，不替换实例，因此不存在「旧 StringVar 被替换 → 旧实例未被 GC」问题。
- `tk.StringVar` 持有 widget 引用，但 widget 销毁时 StringVar 也会被 Tk 释放；多次 `.set()` 不会创建新实例。
- `scanning` 是普通 Python bool，无资源泄漏。
- `result_queue` 替换的安全分析见 #🟢 #19。

## 断言运行结果

运行: `python docs/superpowers/specs/2026-09-16-bug-audit-assertions.py`（python3.11）

- 总断言数: **66**
- 通过: **65**
- 失败: **1**

**唯一失败项**:

| check() 名称 | 涉及行 | 性质 |
|---|---|---|
| `r_frame_rate=0/0 显示 N/A` | `video_checker.py:137-143` | **真实 bug（已记录在 #🟠 高「r_frame_rate="0/0" 时显示 "0.00 fps" 而非 N/A」第 164 行）** |

断言失败原因: `parse_video_info` 在 `r_frame_rate = "0/0"` 时，`num == 0` 且 `den == 0`，但 `if float(den) != 0` 只判断 `den`，故 `fps = 0`，格式化为 `"0.00 fps"` 而非 `"N/A"`。这与静态走查结论一致，是真实代码 bug，**不是断言脚本的 bug**，无需修断言脚本。

**各 section 覆盖范围**:

| section | check 数 | 覆盖目标 |
|---|---|---|
| `scan_video_files` | 5 | 顶层 vs 递归、大小写扩展名、非视频文件排除、空/不存在目录 |
| `parse_video_info — 基本字段与边界` | 9 | is_passing/bitrate/resolution/fps 正常路径；无视频流；r_frame_rate=0/0 与非法字符串 |
| `parse_video_info — 码率 / 采样率 / 阈值` | 11 | stream vs format 码率优先级、缺失码率、阈值边界 `>=`、采样率缺失/非法/低于阈值、双阈值透传 |
| `parse_video_info — 标题 / 大小 / 路径` | 8 | tags 大小写、缺失回退、size 缺失/非数字、relpath 同目录/子目录/跨盘 |
| `_cell_fg 全矩阵` | 16 | 全达标/仅码率不达标/仅采样率不达标/都不达标 × 关键列的红/绿/默认映射 |
| `_truncate_text` | 1 | 短 ASCII 快路径（依赖 Tk root 的真实 measure 测试留待后续） |
| `format_duration` | 6 | 0/59/60/3600/3661 秒、负数异常输入 |
| `format_file_size` | 6 | 0/1023/1024/1MB/1GB、负数 |
| `_get_unique_dest` | 4 | 无冲突、1 次冲突、多重冲突、源文件已含 `_1` 后缀（语义丢失已记录） |

注: `#🟢 低「_get_unique_dest 源文件已含 _1 后缀 → a_1_1.mp4（语义丢失？）」` 断言通过（确认了 a_1_1.mp4 这一行为），但该行为本身就是可疑的——用户看到 `a_1_1.mp4` 不会知道这是「源已含 _1 后缀 + 目标也冲突」叠加产生。属 UX/可读性问题，非崩溃。

---

## 审计概述

### 总发现数

共审计 41 项，按严重度分布：

| 严重度 | 数量 | 说明 |
|---|---|---|
| 🔴 致命 | 3 | 用户数据丢失 / 应用不可恢复 |
| 🟠 高   | 14 | 明显错误结果但有兜底，或可观察到的状态错乱 |
| 🟡 中   | 14 | 边界条件下行为可疑、不一致，或用户体验差 |
| 🟢 低   | 8 | 小毛刺、文案、不影响核心功能 |
| 无（已验证为非问题） | 2 | 线程引用 / StringVar 泄漏类已逐项排除 |
| **合计** | **41** | |

### Top 3 致命 / 高 严重度问题

1. **🔴 致命 — macOS/Linux 上扫描启动即崩溃，UI 永久卡死**（#1）：`run_ffprobe` 访问仅 Windows 存在的 `subprocess.STARTUPINFO` 直接抛 `AttributeError`，且 worker 线程无外层 `try/except`，扫描按钮永久 disabled。
2. **🔴 致命 — worker 线程任何未捕获异常都让 UI 永久卡死**（#9）：worker 死亡后不发 `done`，主线程 `_check_queue` 永不停摆，`scanning` 标志永远 True；这是 macOS 上 #1 现象的根因，也是其他未来异常的「通用卡死放大器」。
3. **🟠 高 — `shutil.move` 跨设备半完成时数据静默丢失**（#5）：copy 成功 + unlink 失败 / copy 中途被中断时，源文件已删、dest 是损坏文件，UI 仅报 `moved += 1`；涉及数据可恢复性的硬问题。

### 静态走查 vs 动态断言的差异

断言脚本运行 66 项，通过 65、失败 1。**唯一失败项（`r_frame_rate="0/0"` 显示 `"0.00 fps"`）** 与静态走查已记录的 🟠 高 finding（#4）完全吻合 —— 静态推断准确，断言脚本本身无 bug，无需修改。这构成了两条独立证据的相互验证：静态分析 + 动态执行在同一 bug 上结论一致。

### 未覆盖项

- **GUI 状态机**：本机 macOS 因 `subprocess.STARTUPINFO` 已知崩溃无法启动扫描，UI 状态只能靠代码读 + git log 反推，未做运行时验证。
- **线程时序**：主线程渲染耗时与 worker `result_queue.put` 节奏的耦合（#🟡 大目录 Label 渲染、#🟢 `_check_queue` 100ms 调度）只能在真实负载下观察，本审计不覆盖。
- **跨设备 / 跨平台**：网络盘、Windows UNC、PyPy / Jython 等异构环境未做实测，依赖静态推断。
- 后续若需覆盖以上项，建议补 GUI 自动化测试 + 性能 profiling + 跨平台 CI。