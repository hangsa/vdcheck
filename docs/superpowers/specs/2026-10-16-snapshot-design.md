# 「定点截图」功能 — 产品 Spec

**日期**: 2026-10-16
**范围**: `video_checker.py` 单文件; 不改 `build.bat` / `video_checker.spec` (ffmpeg.exe 已随 FFmpeg-builds 一起打包到 `ffprobe/` 目录, build 0 增量)

## 1. 背景

`视频码率检查器` 当前只能扫描视频并展示码率/采样率等元数据。用户希望基于**已经检测完的视频列表**, 在**指定的时间点**对**每个视频**抽帧, 直观看到视频画面 (用于: 确认内容、对比「不达标」视频是不是「金玉其外」等场景)。

**核心需求**:
1. 主窗口新增「定点截图」按钮 (位置: 「清除记录」按钮右侧)
2. 点击弹出**输入对话框**: 时间点列表 (默认 2 个, 可增删) + 输出目录选择
3. 用户「确认」后, 后台按 `视频 × 时间点` 顺序调 ffmpeg 抽帧
4. 同时弹出**进度弹窗**: 行=视频, 列=时间点, 每个 cell 显示「加载中」/缩略图/失败标记
5. 抽帧成功: 写到磁盘 (`<output_dir>/<filename>_t<index>_<time>.jpg`, JPEG q=5) + 进度弹窗 cell 显示缩略图
6. 抽帧失败: 该 cell 标记「×」, 继续后续 (不阻断)
7. 进度弹窗有「取消」按钮, 取消时停止后续任务, 已完成文件保留
8. 全部完成后, 弹窗可关闭; 状态栏显示「抽帧完成: X/Y 成功, Z 失败」

## 2. UX 流

```
[主窗口] — 点击「定点截图」按钮
   │
   ▼
[输入对话框]  (模态)
   │  ┌──────────────────────────────────────┐
   │  │ 时间点 1: [00:00:10:00]   [删除]       │
   │  │ 时间点 2: [00:01:30:15]   [删除]       │
   │  │ [+ 添加时间点]                          │
   │  │                                          │
   │  │ 输出目录:  [______________________] [浏览]│
   │  │                                          │
   │  │            [确认]   [取消]                │
   │  └──────────────────────────────────────┘
   │  - 输入校验: HH:MM:SS 范围合法, frame≥0 整数
   │  - 空 / 非法时确认按钮 disabled 或弹错
   ▼
[进度弹窗]  (模态, 阻塞主窗口)
   │  ┌──────────────────────────────────────┐
   │  │ ┌────┬──────┬──────┬──────┐              │
   │  │ │    │ T1   │ T2   │ T3   │  ← 列头=时间点│
   │  │ ├────┼──────┼──────┼──────┤              │
   │  │ │v1  │ [图] │ [图] │ [×]  │  ← 行=文件    │
   │  │ │v2  │[..]  │ [图] │ [图] │              │
   │  │ │v3  │ [图] │[..]  │ [图] │              │
   │  │ └────┴──────┴──────┴──────┘              │
   │  │ 状态: 抽帧中 3/50 (v3)                    │
   │  │              [取消]   [关闭]                │
   │  └──────────────────────────────────────┘
   │  - 缩略图: 160×90 (16:9), 加载中灰色, 成功图, 失败红× 三态
   │  - 进度条或文字: 「抽帧中 X/Y (当前文件)」
   ▼
[完成]
   │  - 弹窗所有 cell 状态稳定
   │  - 主窗口状态栏更新: 「抽帧完成: 50/50 成功, 2 失败」
   │  - 用户点「关闭」/× 关闭弹窗
```

## 3. 架构

### 3.1 模块划分

```
video_checker.py
  ├─ 工具层 (纯函数, 可测)
  │   ├─ get_ffmpeg_path()              # 新增, 对称 get_ffprobe_path
  │   ├─ parse_time_token("HH:MM:SS:frame", fps) -> float  # 新增
  │   ├─ time_token_to_string(seconds, fps) -> str         # 新增
  │   └─ extract_frame(input, seconds, output) -> bool    # 新增, 调 ffmpeg
  │
  ├─ UI 层
  │   ├─ SnapshotInputDialog            # 新增, Toplevel, 模态
  │   │   ├─ 时间点列表 (Listbox + Entry + 添加/删除按钮)
  │   │   ├─ 输出目录选择 (Entry + 浏览按钮)
  │   │   └─ 确认/取消
  │   └─ SnapshotProgressDialog         # 新增, Toplevel, 模态
  │       ├─ 结果表格 (行=文件, 列=时间点, cell=缩略图/loading/×)
  │       ├─ 状态栏
  │       └─ 取消/关闭
  │
  └─ App 集成
      ├─ self.snapshot_btn              # 新增按钮
      ├─ self._on_snapshot_btn_click    # 新增
      ├─ self._snapshot_worker          # 新增, 后台线程入口
      ├─ self._snapshot_check_queue     # 新增, 100ms 收消息
      └─ self.snapshot_cancel_event     # 新增
```

### 3.2 数据流

```
[主线程]
   _on_snapshot_btn_click()
       │ 1. 弹 SnapshotInputDialog, 取 (time_tokens, output_dir)
       │ 2. 创建 SnapshotProgressDialog
       │ 3. 起后台 worker
       ▼
   _snapshot_worker()      [后台线程]
       │ for each file × time:
       │   ├─ result_queue.put(('cell_start', (row, col)))   # 灰 loading
       │   ├─ seconds = parse_time_token(token, video.fps)
       │   ├─ success = extract_frame(file, seconds, out_path)
       │   ├─ if success: cell_done (缩略图)
       │   └─ else: cell_failed (×)
       │ result_queue.put(('progress', (done, total, current_file)))
       │ result_queue.put(('done', None))
       ▼
   _snapshot_check_queue() [主线程 100ms 轮询]
       │ 收 ('cell_start'|'cell_done'|'cell_failed'|'progress'|'done')
       │ cell_*: 改对应 cell 状态
       │ progress: 改状态栏文字
       │ done: 弹窗状态稳定, 停止轮询
```

### 3.3 弹窗状态机 (cancel 流程)

- `running`: worker 跑, 弹窗可点「取消」/「关闭」
- `cancelling`: 用户点「取消」, `cancel_event.set()`; worker 收 `done` 消息时识破已取消
- `cancelled`: worker 跳过后续, 弹窗状态切到「已取消」, 「关闭」可关
- `completed`: 全部跑完 (含失败), 「关闭」可关

每个 `SnapshotProgressDialog` 实例有 `_state: str` 字段; `mark_complete` 切状态, 「关闭」按钮仅在 `completed/cancelled` 时关闭弹窗 (running/cancelling 时弹确认)。

## 4. 关键设计

### 4.1 ffmpeg 路径解析 — `get_ffmpeg_path()`

完全对称 `get_ffprobe_path()`: 优先 `sys._MEIPASS/ffprobe/ffmpeg.exe`, 退到 `shutil.which('ffmpeg')` 或 `'ffmpeg'`。**无需改 build.bat / spec** (ffmpeg.exe 已经在 `ffprobe/` 目录里)。

```python
@lru_cache(maxsize=1)
def get_ffmpeg_path() -> str:
    if getattr(sys, 'frozen', False):
        base_dir = sys._MEIPASS
        local_ffmpeg = os.path.join(base_dir, 'ffprobe', 'ffmpeg.exe')
        if os.path.exists(local_ffmpeg):
            return local_ffmpeg
    return shutil.which('ffmpeg') or 'ffmpeg'
```

### 4.2 时间点解析 — `parse_time_token(token, fps)`

`HH:MM:SS:frame` 格式:
- HH: 0+ 整数
- MM: 0-59
- SS: 0-59
- frame: 0+ 整数
- 整体不为空, 4 段
- 解析失败抛 `ValueError`, UI 层捕获后红字提示

返回: `HH*3600 + MM*60 + SS + frame/fps` (float, 秒)

```python
def parse_time_token(token: str, fps: float) -> float:
    parts = token.strip().split(':')
    if len(parts) != 4:
        raise ValueError(f"时间点格式应为 HH:MM:SS:frame, 得到: {token!r}")
    h, m, s, f = (int(p) for p in parts)   # ValueError 转抛
    if h < 0 or m < 0 or s < 0 or f < 0 or m > 59 or s > 59:
        raise ValueError(f"时间点数值越界: {token!r}")
    if fps <= 0:
        raise ValueError(f"视频帧率无效 ({fps}), 无法解析 frame 字段")
    return h * 3600 + m * 60 + s + f / fps
```

### 4.3 文件名规范化 — `time_token_to_string`

进度弹窗列头 / 输出文件名 / 弹窗列头显示都需规范化时间点:

```python
def time_token_to_string(token: str) -> str:
    """把 '00:01:23:15' 转成 '00-01-23-15' (文件名安全, Windows 不吃 ':')"""
    return token.replace(':', '-')
```

用于: 列头 `T1: 00-01-23-15`、输出文件 `video1_t1_00-01-23-15.jpg`。

### 4.4 抽帧 — `extract_frame(input, seconds, output_path)`

抽帧时同时缩放到 160 px 宽 (高度按原比例), JPEG q=5 写入 output_path。**磁盘上保留的就是缩略图**, 弹窗直接 `PhotoImage(file=...)` 显示, 内存与磁盘都只存一份。

```python
def extract_frame(input_path: str, seconds: float, output_path: str) -> bool:
    """用 ffmpeg 在 seconds 处抽 1 帧, 缩放到 160 px 宽, JPEG q=5 写入 output_path。返回是否成功。"""
    try:
        ffmpeg = get_ffmpeg_path()
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        result = subprocess.run(
            [
                ffmpeg, '-y',                  # 覆盖同名
                '-ss', f'{seconds:.6f}',       # seek 到时间点
                '-i', input_path,
                '-frames:v', '1',              # 仅 1 帧
                '-vf', 'scale=160:-1',         # 缩到 160 px 宽, 高度按比例
                '-q:v', '5',                   # JPEG 质量
                output_path,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,         # 静默
            stderr=subprocess.PIPE,
            timeout=30,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return result.returncode == 0 and os.path.exists(output_path)
    except (subprocess.TimeoutExpired, OSError):
        return False
```

注: ffmpeg `-ss <time>` 是**时间点 seek** (vs `-frames:v 1` 仅取 1 帧)。用户输 HH:MM:SS:frame, 我们换算成秒后传 `-ss`。ffmpeg 会 seek 到最近关键帧并解码到该时间, 输出帧**接近**但**未必精确**于 frame 字段 (差 0-1 帧)。**这是 ffmpeg 行为决定的, 本 spec 不解决 ffmpeg 内部 keyframe 粒度问题**; 真要「字节级精确」需用 `-vf select=eq(n\,N)` 整段解码, 性能差 1-2 个数量级, 不在范围。

### 4.5 输入对话框 — `SnapshotInputDialog`

`tk.Toplevel`, `transient(parent)` + `grab_set()` (模态)。

布局:
```
时间点 1:  [Entry: 00:00:10:00]  [删除]
时间点 2:  [Entry: 00:01:30:15]  [删除]
[+ 添加时间点]

输出目录:  [Entry]  [浏览]

[确认]  [取消]
```

**输入校验**:
- 时间点 Entry: 失焦或点「确认」时调 `parse_time_token` 试探, 失败则 Entry 边框红 + 错误标签
- 输出目录: 必须存在且可写, 否则标红
- 「确认」按钮: 任一时间点 / 目录非法时 disabled

**交互**:
- 「+ 添加时间点」: append 新 Entry, 焦点跳到新 Entry
- 「删除」: 仅在 ≥2 个时间点时显示 (≥1 也行, 但至少 1 个有效)
- 「确认」: 校验通过 → 调 `parent._start_snapshot(time_tokens, output_dir)`, 关闭自身
- 「取消」/×: 关闭, 不启动 worker

**默认时间点**:
- 时间点 1: `00:00:10:00` (10 秒第 0 帧)
- 时间点 2: `00:01:30:00` (1 分 30 秒第 0 帧)
- 用户可立即编辑

### 4.6 进度弹窗 — `SnapshotProgressDialog`

`tk.Toplevel`, 模态。**自带的简单 scrollable grid (不复用 `VideoGridView`)**:
- 原因: `VideoGridView` 是为 12 列固定 + 列宽可拖设计的; 进度弹窗的列数随用户输入 (1-N 个时间点) 动态变, 不需要列宽拖动 / 排序 / 列宽持久化, 复用其 virtual scroll 不值
- 结构: `Canvas` + 内嵌 `Frame`, `Frame` 内用 `grid` 布局: 行=文件, 列=时间点
- 缩略图用 `tk.Label(image=...)`; 文字态用 `tk.Label(text=...)`
- 数据规模: 100 文件 × 6 列 = 600 widget 可接受 (主窗口 grid 的虚拟滚动池是 40, 这里固定 widget 600 是另一种合理)
- 横向滚动: 列头固定; 纵向滚动: 整框在 Canvas 内

**Cell 三态** (每个 cell 是一个 `tk.Label`, 文本与图片互斥):
- `idle` (初始): cell 文本 = `'…'`, fg = gray (用户视觉: 等待)
- `loading`: cell 文本 = `'…'`, fg = blue (loading 短暂, 视觉差异小; 实际可与 idle 同色)
- `done`: cell `image` = PhotoImage (160×90), `text = ''`
- `failed`: cell 文本 = `'×'`, fg = red, font bold large

**缩略图加载**:
- ffmpeg 已直接生成 160×90 jpg 吗? **不**, ffmpeg 输出是原视频分辨率 (1080p, 4k 都有)
- 我们需要: **a)** 输出原图, 弹窗里 PIL 缩放显示; **b)** ffmpeg 加 `-vf scale=160:-1` 直接出缩略图
- 选 **b**: ffmpeg `-vf scale=160:-1` 一次缩放到位, 弹窗 `PhotoImage(file=...)` 直接加载, 内存与磁盘双省
- 注: 磁盘保存的也是 160×90 缩略图, 不能再放大 (如需原图, 后续可加「原图」选项, 不在本期范围)

**`PhotoImage` 引用持有**:
- Tk 的 PhotoImage 引用计数到 0 会被 GC, 显示时变白
- 进度弹窗需维护 `self._photo_refs: list[PhotoImage]`, 每次新缩略图 append
- 弹窗关闭时清空

**取消按钮**:
- 点击 → `parent.snapshot_cancel_event.set()`
- worker 在每个 cell 处理前检查; 取消后: 当前 cell 让其跑完 (ffmpeg 已起), 后续不再起
- 弹窗状态切到「已取消」; 状态栏文字: 「抽帧已取消, 已完成 X/Y」
- 「关闭」按钮在 cancelled / completed 状态都可用

**关闭弹窗**:
- 弹窗 `WM_DELETE_WINDOW` → 弹「确认关闭?」(仅 running/cancelling 时; completed/cancelled 静默关闭)
- 关闭 → `self._photo_refs.clear()`, `dialog.destroy()`

### 4.7 App 集成

**按钮位置** (line 972 附近):
```python
ttk.Button(std_frame, text="清除记录", command=self._clear_records).pack(
    side='right', padx=(0, 272)
)
# 新增:
ttk.Button(std_frame, text="定点截图", command=self._on_snapshot_btn_click).pack(
    side='right', padx=(0, 5)   # 紧贴「清除记录」左侧
)
```

注: 现有「清除记录」用 `padx=(0, 272)` 把按钮**向左推** 272 px, 与 Row 1「浏览」对齐。定点截图用 `padx=(0, 5)` 紧贴其左, 二者作为一个 button group 在行右端。

**`_on_snapshot_btn_click`**:
```python
def _on_snapshot_btn_click(self):
    if self.scanning:
        messagebox.showinfo("提示", "正在检测中, 请等待完成后再截图。")
        return
    if not self.video_results:
        messagebox.showinfo("提示", "请先扫描视频。")
        return
    dlg = SnapshotInputDialog(self.root, default_times=2)
    self.root.wait_window(dlg)  # 模态
    if dlg.result is None:
        return
    time_tokens, output_dir = dlg.result
    self._start_snapshot(time_tokens, output_dir)
```

**`_start_snapshot`**:
```python
def _start_snapshot(self, time_tokens: list[str], output_dir: str):
    self.snapshot_cancel_event = threading.Event()
    self.snapshot_result_queue = queue.Queue()
    self.snapshot_progress_dialog = SnapshotProgressDialog(
        self.root,
        files=self.video_results,
        time_tokens=time_tokens,
        output_dir=output_dir,
    )
    t = threading.Thread(
        target=self._snapshot_worker,
        args=(time_tokens, output_dir),
        daemon=True,
    )
    t.start()
    self._snapshot_check_queue()
```

**`_snapshot_worker`**:
```python
def _snapshot_worker(self, time_tokens, output_dir):
    files = self.video_results
    n_files = len(files)
    n_times = len(time_tokens)
    total = n_files * n_times
    done = 0
    for r, info in enumerate(files):
        if self.snapshot_cancel_event.is_set():
            break
        for c, token in enumerate(time_tokens):
            if self.snapshot_cancel_event.is_set():
                break
            self.snapshot_result_queue.put(('cell_start', (r, c)))
            self.snapshot_result_queue.put(('progress', (done, total, info.title)))
            try:
                seconds = parse_time_token(token, info.frame_rate_fps)
            except ValueError:
                self.snapshot_result_queue.put(('cell_failed', (r, c)))
                done += 1
                continue
            out_name = f"{os.path.splitext(info.title)[0]}_t{c+1}_{time_token_to_string(token)}.jpg"
            out_path = os.path.join(output_dir, out_name)
            ok = extract_frame(info.full_path, seconds, out_path)
            if ok:
                self.snapshot_result_queue.put(('cell_done', (r, c, out_path)))
            else:
                self.snapshot_result_queue.put(('cell_failed', (r, c)))
            done += 1
    self.snapshot_result_queue.put(('done', (done, total, self.snapshot_cancel_event.is_set())))
```

**`_snapshot_check_queue`**:
```python
def _snapshot_check_queue(self):
    try:
        while True:
            msg_type, payload = self.snapshot_result_queue.get_nowait()
            if msg_type == 'cell_start':
                r, c = payload
                self.snapshot_progress_dialog.mark_loading(r, c)
            elif msg_type == 'cell_done':
                r, c, path = payload
                self.snapshot_progress_dialog.mark_done(r, c, path)
            elif msg_type == 'cell_failed':
                r, c = payload
                self.snapshot_progress_dialog.mark_failed(r, c)
            elif msg_type == 'progress':
                done, total, current = payload
                self.snapshot_progress_dialog.update_status(done, total, current)
            elif msg_type == 'done':
                done, total, cancelled = payload
                self.snapshot_progress_dialog.mark_complete(done, total, cancelled)
                self._on_snapshot_complete(done, total, cancelled)
                return
    except queue.Empty:
        pass
    if self.snapshot_progress_dialog and not self.snapshot_progress_dialog._closed:
        self.root.after(100, self._snapshot_check_queue)
```

**`_on_snapshot_complete`**:
```python
def _on_snapshot_complete(self, done: int, total: int, cancelled: bool):
    failed = self.snapshot_progress_dialog._failed_count
    succeeded = done - failed
    if cancelled:
        self.status_var.set(f"抽帧已取消: 已完成 {done}/{total} ({succeeded} 成功, {failed} 失败)")
    else:
        self.status_var.set(f"抽帧完成: {succeeded}/{total} 成功, {failed} 失败")
```

## 5. 错误与边界

### 5.1 时间点解析失败
- `parse_time_token` 抛 `ValueError` → cell 标「×」, 继续后续
- 弹窗里: 输入时实时校验, 失败 Entry 红边
- 「确认」按钮: 任一非法时 disabled

### 5.2 视频无 fps (`frame_rate_fps == 0`)
- `parse_time_token` 抛 `ValueError` (`fps <= 0`)
- 该文件所有时间点全部 cell 标「×」
- 状态栏在 `_on_snapshot_complete` 提示

### 5.3 ffmpeg 不存在
- macOS dev: `/opt/homebrew/bin/ffmpeg` 已存在, 不触发
- Windows 部署: 打包时 ffmpeg.exe 已在 `ffprobe/`, 通过 `get_ffmpeg_path` 找到
- 极端: `shutil.which('ffmpeg')` 也 None → 弹窗打开前 `get_ffmpeg_path()` 返回字符串 `'ffmpeg'`, 实际调用失败
- 处理: `_start_snapshot` 入口 `assert os.path.exists(get_ffmpeg_path()) or shutil.which('ffmpeg')`, 否则 messagebox.showerror

### 5.4 输出目录不存在 / 不可写
- 「确认」时检查, 标红
- 极端: 抽帧到一半目录被删 → ffmpeg returncode != 0 → cell 标「×」, 继续后续

### 5.5 视频时长 < 时间点
- ffmpeg `-ss` 越界 → 输出视频最后一帧 (或黑帧), 文件存在, 标「done」
- 不在范围: 不去 ffprobe 取 duration 预校验 (N×M 次额外 ffprobe, 慢)

### 5.6 取消时正在跑的 ffmpeg
- cancel_event 在 cell 循环间隙检查; **当前 ffmpeg 进程不强制 kill** (subprocess 启动后无法跨线程安全 kill on macOS)
- 影响: 用户点取消后, 可能多抽 1-2 帧; 接受
- 严格取消不在范围

### 5.7 主窗口扫描中
- 「定点截图」入口检查 `self.scanning` → messagebox 拒绝

### 5.8 0 检测结果时点按钮
- 入口检查 `self.video_results` → messagebox 拒绝

### 5.9 输出目录为空但已存在
- 允许, 正常写入
- 「确认」时不要求空目录 (用户可能想覆盖上次结果)

### 5.10 时间点列表为空 (用户删光)
- 「确认」时检查 `len(time_tokens) >= 1`, 否则 disabled
- 「- 删除」按钮在 ≥2 时显示, 隐藏到只剩 1 个 (与默认 2 启动对应)

## 6. 测试

### 6.1 新增一次性断言

`docs/superpowers/specs/2026-10-16-snapshot-assertions.py`:

纯函数单元测试 (无 Tk):
- `parse_time_token`:
  - 合法 `00:00:10:00` @ 30fps → 10.0
  - 合法 `01:30:45:15` @ 30fps → 5445.5
  - 合法 `00:00:00:1` @ 60fps → 1/60 ≈ 0.01666...
  - frame=0 @ 任意 fps → 整数秒
  - 非法: 段数错 (3 段 / 5 段) → ValueError
  - 非法: 负数 / MM>59 / SS>59 → ValueError
  - 非法: 非数字 → ValueError
  - 非法: fps=0 → ValueError
- `time_token_to_string`:
  - `00:00:10:00` → `00-00-10-00`
  - `01:30:45:15` → `01-30-45-15`
- `extract_frame` 真实调用 (需 ffmpeg 在 PATH):
  - 跳过 (用 `@pytest.mark.skipif(not ffmpeg)` 风格: 检测不到 ffmpeg 时 skip 整段)
  - 正常 mp4 → 输出 jpg, 文件存在, 大小 > 0, returncode=0
  - 不存在文件 → returncode != 0, return False
  - 损坏文件 → returncode != 0, return False
  - seconds=0 → 首帧

### 6.2 现有断言脚本回归

```
python docs/superpowers/specs/2026-09-16-bug-audit-assertions.py
python docs/superpowers/specs/2026-09-28-container-format-assertions.py
python docs/superpowers/specs/2026-09-29-dnd-parsing-assertions.py
python docs/superpowers/specs/2026-10-13-sort-headers-assertions.py
python docs/superpowers/specs/2026-10-14-virtual-scroll-assertions.py
```

全部仍应退出 0。本改动只新增 `get_ffmpeg_path / parse_time_token / time_token_to_string / extract_frame` 4 个纯函数 + 2 个新类, 不修改现有函数签名/行为。

### 6.3 手动验收

`python video_checker.py` 启动:

1. **按钮位置**: 「清除记录」右侧 5px 是「定点截图」
2. **空列表**: 0 检测结果时点「定点截图」→ messagebox「请先扫描视频」
3. **扫描中**: 启动扫描后点「定点截图」→ messagebox「请等待扫描完成」
4. **输入对话框**:
   - 默认 2 个时间点 (10:00, 1:30:00)
   - 「+ 添加」→ 第 3 个时间点
   - 「删除」→ 移除选中
   - 输错 (e.g. `99:99:99:99`) → 边框红, 「确认」disabled
   - 输出目录选已存在目录 → 可点确认
   - 输出目录选不存在 → 标红
5. **进度弹窗**:
   - 列头 = `T1: 00-00-10-00`, `T2: 00-01-30-00`
   - cell 初始 = `…` 灰
   - 抽帧中 cell = `…` 蓝 (loading) 短暂
   - 完成 cell = 160×90 缩略图
   - 失败 cell = 红 `×`
6. **取消**:
   - 跑一半点取消 → 后续 cell 保持 `…`, 状态栏「抽帧已取消: 5/20 (3 成功, 2 失败)」
   - 磁盘: 已成功的 3 张 jpg 保留
7. **完成**:
   - 全部完成后状态栏「抽帧完成: 18/20 成功, 2 失败」
   - 弹窗「关闭」可关
8. **磁盘文件**:
   - 打开输出目录: `video1_t1_00-00-10-00.jpg`, `video1_t2_00-01-30-00.jpg`, ...
   - 命名正确, 可被系统图片浏览器打开
9. **重跑同参数**: 覆盖旧 jpg (用户在 5.9 选择的行为)
10. **回归**: 「开始检测」「清除记录」「移动达标/全部」「拖拽」「列宽」「排序」「虚拟滚动」「目标路径」全不受影响

## 7. 风险

- **ffmpeg 帧精度**: `-ss <time>` 受 keyframe 粒度影响, 差 0-1 帧, **不达真「frame-precise」标准**。若用户强烈要求字节级精确, 需切到 `-vf select=eq(n\,N)` 整段解码, 性能差 1-2 个数量级, 不在本期范围
- **缩略图 160×90 = 信息损失**: 若用户想看 4K 细节, 缩略图不够; 后续可加「原图」按钮, 不在本期
- **macOS dev 抽 Windows 部署文件**: ffmpeg 在两平台都打包, 路径解析统一, 风险低
- **取消时 ffmpeg 进程不强制 kill**: 接受, 文档化
- **进度弹窗开在主窗口上**: 模态阻塞主窗口操作 (避免竞态), 用户无法点「清除记录」; 接受
- **大量文件 (100+) × 多个时间点**: 单线程 ~1-2 分钟; 进度条/文字反馈, 用户不会以为卡死
- **PhotoImage 引用**: 必须持有, 否则 GC 白屏; 测试覆盖

## 8. 不在范围 (YAGNI)

- 不做并发 (线程池 / ffmpeg filter 一次出 N 帧) — 复杂度不值
- 不做 ffmpeg 进程级强制 kill — 跨平台复杂, 用户点取消后多 1-2 帧可接受
- 不做真「frame-precise」 (`-vf select`) — 性能 10x 差, 用户可能根本没察觉差 1 帧
- 不做 ffprobe 预校验视频时长 — N×M 次额外调用, 慢
- 不做缩略图缩放 (固定 160×90) — 用户要原图, 下期加
- 不做主窗口行内 cell 缩略图列 — 已在弹窗里做
- 不做保存「截图任务」配置 (用户重跑每次填时间点) — 后续可加
- 不做多选行 / 过滤 (达标 / 不达标) — 弹窗作用范围固定为全部
- 不改 `build.bat` / `video_checker.spec` — ffmpeg 已随 FFmpeg-builds 一起打包
- 不动现有 `VideoGridView` 公开 API (复用其实例化即可)

---

## 9. 后续可扩展 (非本期, 留作下期讨论)

- 截图「原图」开关 (`-vf scale` vs 原分辨率)
- 「frame-precise」模式 (用 `-vf select=eq(n\,N)`, 加「精准」checkbox)
- 时间点模板保存 / 加载 (JSON 配置)
- 弹窗内点击缩略图放大 (原图)
- 缩略图按时间点/视频切换布局 (现是文件 × 时间点)
