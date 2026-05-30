# VoiceFlow

**本地语音输入助手** — 按住 F10 或 F11 说话，松开后自动识别并粘贴到光标位置。无需云端，无需切换应用，说完即出字。

---

## 目录

- [特性](#特性)
- [快速开始](#快速开始)
- [架构](#架构)
- [使用方式](#使用方式)
- [配置](#配置)
- [模块详解](#模块详解)
- [项目结构](#项目结构)
- [开发](#开发)
- [测试](#测试)
- [打包与安装](#打包与安装)
- [系统要求](#系统要求)
- [FAQ](#faq)

---

## 特性

- **双热键模式** — F10 智能粘贴（自动检测终端/普通窗口），F11 终端粘贴（强制 `ctrl+shift+v`）
- **零启动延迟录音** — 声卡 Stream 始终保持预热，加上 300ms pre-roll 环形缓冲区，捕获说话的第一毫秒
- **自动粘贴** — 识别结果直接插入当前光标位置（通过剪贴板模拟粘贴）
- **智能窗口检测** — 自动判断当前窗口类型，选择正确的粘贴快捷键
- **纯本机运行** — 音频不走外网，隐私安全
- **低延迟** — 录音到出字端到端 1-3 秒
- **悬浮指示器** — mac 风格的录音/识别状态胶囊
- **提示音与通知** — 开始、结束、出错均有声音和桌面通知反馈
- **可替换 ASR 后端** — 通过配置切换 vLLM 模型

---

## 快速开始

### 1. 安装系统依赖

```bash
sudo apt install xclip xdotool libportaudio2
```

### 2. 启动 ASR 服务

需要本地 vLLM 服务（推荐 qwen3-asr）：

```bash
pip install "vllm[audio]"
python -m vllm.entrypoints.openai.api_server \
  --model /path/to/qwen3-asr-1.7B \
  --host 0.0.0.0 --port 8000
```

### 3. 安装与运行

```bash
uv venv
uv pip install -e ".[dev]"
.venv/bin/python main.py
```

按住 **F10**（智能粘贴）或 **F11**（终端粘贴）开始录音，松开后自动识别并粘贴。`Ctrl+C` 退出。

---

## 架构

### 双进程架构

```
┌──────────────────────────────────────────────────────┐
│                  voiceflow（一个 App）                │
│                                                      │
│   ┌────────────────────┐     ┌───────────────────┐  │
│   │  Tauri Rust        │     │  Python 边车       │  │
│   │  /usr/bin/voiceflow│     │  daemon.py         │  │
│   │                    │     │                    │  │
│   │  负责：            │     │  负责：            │  │
│   │  - 窗口/托盘管理   │     │  - 录音（sounddevice）│  │
│   │  - UI 入口         │     │  - ASR 调用（HTTP）  │  │
│   │  - 系统集成        │     │  - 剪贴板粘贴        │  │
│   └────────┬───────────┘     │  - 通知/提示音       │  │
│            │ stdin/stdout   │  - 悬浮指示器        │  │
│            │ JSON           └───────────────────┘  │
│            └───────────────────────────────────────│
└──────────────────────────────────────────────────────┘
```

Tauri Rust 二进制（`/usr/bin/voiceflow`）是 App 入口，Python 边车（`daemon.py`）负责所有语音处理逻辑。两者通过 stdin/stdout JSON 行通信。

### 状态机核心

整个应用由 `StateMachine` 驱动，所有模块交互通过状态转换完成：

```
IDLE ──[F10/F11按下]──→ RECORDING
                              │
                     [F10/F11松开] │
                              ↓
                    TRANSCRIBING（调用 ASR）
                              │
                         [识别成功] │
                              ↓
                    PASTING（粘贴文字）
                              │
                         [完成] │
                              ↓
                         IDLE

任意状态 ──[出错]──→ ERROR ──[恢复]──→ IDLE
```

**状态列表**：`IDLE` `RECORDING` `TRANSCRIBING` `PASTING` `ERROR`

### 线程模型

项目使用**纯线程**（无 async/await），原因：
- sounddevice 和 python-xlib 只支持回调式 API
- 并发需求简单（1 个语音流），瓶颈在 ASR 网络 I/O，GIL 不影响

关键线程：
| 线程 | 职责 | 通信方式 |
|------|------|----------|
| 主线程 | `signal.pause()` 循环，状态机回调 | — |
| 热键线程 | X11 event loop（`XGrabKey`） | 直接调用 `_on_hotkey_press` |
| 音频回调线程 | sounddevice 实时回调 | 写 `deque`（线程安全）+ 命令队列 |
| 指示器线程 | Tkinter 主循环 | `queue.SimpleQueue` |

---

## 使用方式

```
普通应用：聚焦输入框 → 按住 F10 → 说话 → 松开 F10 → 文字自动出现在光标处
终端应用：聚焦终端 → 按住 F10（自动检测）或 F11（强制终端模式）→ 说话 → 松开 → 文字自动粘贴
```

### 模式区别

| 热键 | 窗口检测 | 粘贴快捷键 | 适用场景 |
|------|----------|-----------|----------|
| F10 | 自动智能检测 | `ctrl+v` 或 `ctrl+shift+v` | 大多数场景 |
| F11 | 跳过检测 | `ctrl+shift+v`（强制） | 智能检测失败时 |

---

## 配置

所有运行时行为通过 `config.yaml` 配置：

```yaml
# ══════════════════════════════════════════════
# 热键配置
# ══════════════════════════════════════════════
hotkey:
  smart_combo: f10       # 智能粘贴模式（自动检测窗口类型）
  terminal_combo: f11     # 终端粘贴模式（强制 ctrl+shift+v）

# ══════════════════════════════════════════════
# 音频配置
# ══════════════════════════════════════════════
audio:
  sample_rate: 16000    # 采样率（固定 16kHz）
  channels: 1            # 声道数（固定单声道）
  dtype: int16           # 数据格式
  max_record_seconds: 60 # 单次最长录音时间

# ══════════════════════════════════════════════
# ASR 配置
# ══════════════════════════════════════════════
asr:
  endpoint: http://127.0.0.1:8000    # vLLM 服务地址
  model: /path/to/qwen3-asr-1.7B     # 模型路径或名称
  timeout_seconds: 20                 # 请求超时时间
  language: zh                       # 语言
  prompt: ""                          # 可选的 hint prompt

# ══════════════════════════════════════════════
# 粘贴配置
# ══════════════════════════════════════════════
paste:
  enabled: true
  smart_mode: true                   # 是否启用智能窗口检测
  default_shortcut: ctrl+v           # 普通窗口粘贴快捷键
  terminal_shortcut: ctrl+shift+v    # 终端窗口粘贴快捷键
  restore_clipboard: true            # 是否恢复原剪贴板内容

  # 终端窗口类名（通过 xprop WM_CLASS 获取）
  terminal_classes:
    - gnome-terminal
    - konsole
    - xfce4-terminal
    - kitty
    - alacritty
    - wezterm
    - tilix
    - xterm
    - org.gnome.terminal

  # 终端窗口标题关键词（用于兜底识别）
  terminal_title_keywords:
    - terminal
    - 终端
    - bash
    - zsh
    - fish
    - ssh
    - tmux
    - vim
    - nvim

# ══════════════════════════════════════════════
# UX 配置
# ══════════════════════════════════════════════
ux:
  indicator: true                 # 录音悬浮指示器（mac 风格胶囊）
  indicator_follow_pointer: true  # 指示器是否跟随鼠标指针
  start_beep: true                # 开始录音提示音
  end_beep: true                  # 结束录音提示音
  error_beep: true                # 出错提示音
  notify: true                    # 桌面通知

# ══════════════════════════════════════════════
# 日志配置
# ══════════════════════════════════════════════
log:
  level: INFO
  file: ./logs/voice_input.log
```

### 配置验证

启动时 `utils.load_config()` 会自动验证配置：
- 缺少必填字段 → 抛出 `ConfigValidationError`，程序退出
- 类型错误（如 `sample_rate` 不是整数）→ 同样报错

---

## 模块详解

### audio_recorder.py — 零延迟录音

**核心设计：pre-roll 环形缓冲区 + 持续开启的声卡 Stream**

```
程序启动
    │
    ▼
声卡 Stream 打开（始终保持）────────┐
    │                               │
    ▼                               │
持续写入 pre-roll deque（最近300ms）  │
    │                               │
    ▼                               ▼
 [F10 按下]          [F10 松开]
    │                   │
    │ 把 pre-roll 拷贝到    │
    │ _frames，继续录     │
    │                   ▼
    │               停止，合并
    │               _frames → WAV
    │                   │
    └───────────────────┘
```

`PRE_ROLL_MS = 300` 是硬编码常量，在 [audio_recorder.py:27](audio_recorder.py#L27)。如需调整，直接修改此值后重新打包。

### asr_client.py — ASR HTTP 客户端

- 调用 OpenAI 兼容的 `/v1/audio/transcriptions` 接口
- 输入：16kHz mono int16 WAV 文件
- 输出：`ASRResult(text, duration_ms, request_id)`
- 内置重试：失败后等 0.3s 再试一次
- 异常类型：`ASRTimeoutError` `ASRUnavailableError` `ASREmptyError`

### window_detector.py — 智能窗口检测

`PasteModeDetector` 通过两步判断当前窗口类型：

1. **`xprop WM_CLASS`** — 获取窗口类名（如 `gnome-terminal`）
2. **`xdotool getwindowname`** — 获取窗口标题

匹配 `terminal_classes`（类名）或 `terminal_title_keywords`（标题关键词）任一即判定为终端，使用 `terminal_shortcut`（`ctrl+shift+v`），否则用 `default_shortcut`（`ctrl+v`）。

### text_injector.py — 剪贴板粘贴

粘贴流程（`restore_clipboard: true` 时）：

```
1. xclip -sel clip -o → 保存原剪贴板
2. xclip -sel clip → 设置新文本
3. xdotool key ctrl+v → 触发粘贴
4. 等待 0.5s
5. xclip -sel clip → 恢复原剪贴板
```

`restore_clipboard` 为 `false` 时跳过步骤 1 和 5。

### hotkey_manager.py — 全局热键（X11）

- 使用 `XGrabKey` 在 root window 注册热键
- 支持多 modifier 组合：普通、Mod2Mask（NumLock）、LockMask（CapsLock）
- 通过 **peek 下一事件** 过滤 auto-repeat（键按住不放时的重复事件）
- 双热键支持：传入 `{"f10": "smart", "f11": "terminal"}` 字典
- `on_release` 回调接收 `combo_mode` 参数，标识是哪个热键触发

### voice_indicator.py — 悬浮指示器

- Tkinter 实现，运行在独立线程
- 通过 `queue.SimpleQueue` 接收命令（`show_listening` / `show_transcribing` / `hide` / `stop`）
- 支持跟随鼠标指针（Xlib `query_pointer`）
- mac 风格胶囊：REC 状态有脉冲动画，ASR 状态有三点跳动动画

### notifier.py — 提示音与通知

- 提示音：`beeps/` 目录下的 WAV 文件（`start.wav` `end.wav` `error.wav`），用 `aplay` 播放
- 桌面通知：用 `notify-send` 实现

### state_machine.py — 状态机

- 线程安全：`threading.Lock` 保护所有状态转换
- 只能通过 `transition()` 或 `force_reset()` 修改状态
- `VALID_TRANSITIONS` 定义合法转换路径，非法转换会记录警告并抛异常

---

## 项目结构

```
voiceflow/
├── main.py                 # 入口，组装所有模块，状态机回调
├── state_machine.py        # 状态机（线程安全，5个状态）
├── audio_recorder.py       # 录音（pre-roll + 持续声卡）
├── asr_client.py           # ASR HTTP 客户端
├── text_injector.py        # 剪贴板粘贴
├── hotkey_manager.py       # 全局热键（X11 XGrabKey）
├── window_detector.py      # 智能窗口检测（xprop/xdotool）
├── voice_indicator.py      # 悬浮指示器（Tkinter 独立线程）
├── notifier.py             # 提示音 & 桌面通知
├── utils.py               # 配置加载 & 日志
├── config.yaml            # 配置文件
├── pyproject.toml         # 依赖管理（uv）
├── CLAUDE.md              # Claude Code 指南
├── tests/                 # 单元测试
│   ├── test_state_machine.py
│   ├── test_asr_client_mock.py
│   └── test_utils.py
├── voiceflow-ui/          # Tauri 前端（React）
│   └── src/
│       ├── App.tsx
│       ├── main.tsx
│       └── components/
├── scripts/               # 辅助脚本
│   ├── run.sh
│   ├── voiceflow.service  # systemd user service
│   └── packaging/
└── build/                 # 打包输出目录
    └── deb-package/       # deb 包结构
```

---

## 开发

### 环境搭建

```bash
# 创建虚拟环境
uv venv
uv pip install -e ".[dev]"

# 运行（开发模式，不走 systemd）
.venv/bin/python main.py
```

### 日志

日志默认输出到 `./logs/voice_input.log`（可在 `config.yaml` 中修改）。

查看实时日志：

```bash
tail -f logs/voice_input.log
```

### 修改预录缓冲区时间

`audio_recorder.py:27` 的 `PRE_ROLL_MS = 300` 控制 pre-roll 时长。修改后需重新打包安装：

```bash
# 方案 A：直接覆盖已安装的文件（开发调试用）
sudo cp audio_recorder.py /opt/voiceflow/audio_recorder.py
systemctl --user restart voiceflow

# 方案 B：重新打包（正式发布）
# 见"打包与安装"章节
```

---

## 测试

```bash
# 运行所有测试
uv run pytest tests/ -v

# 单个测试文件
uv run pytest tests/test_state_machine.py -v

# 覆盖率
uv run pytest tests/ --cov=. --cov-report=term-missing
```

### 测试内容

| 文件 | 覆盖范围 |
|------|----------|
| `test_state_machine.py` | 线程安全（20 并发转换）、非法转换、force_reset |
| `test_asr_client_mock.py` | 超时、连接错误、500 错误、重试逻辑、空结果 |
| `test_utils.py` | 配置验证（必填字段、类型、边界） |

---

## 打包与安装

### Debian 包结构

```
voiceflow_0.1.0_amd64.deb
├── DEBIAN/
│   ├── control
│   ├── postinst          # 安装后脚本（启动 systemd 服务）
│   └── prerm             # 卸载前脚本
├── usr/bin/voiceflow     # Tauri Rust 二进制
└── opt/voiceflow/
    ├── audio_recorder.py  # Python 模块
    ├── asr_client.py
    ├── ...
    ├── voiceflow_py/
    │   └── daemon.py      # Python 边车入口
    └── python-libs/        # 打包的 Python 依赖
```

### 构建 Python deb 包（必须使用 Python 3.12）

**重要**：NumPy C 扩展（`.so` 文件）必须为 cp312 编译。若用 Python 3.13 打包，运行时系统 Python 3.12 会报 `ModuleNotFoundError: No module named 'numpy._core._multiarray_umath'`。

```bash
# 1. 创建 Python 3.12 环境（若不存在）
conda create -n build-py312 python=3.12 -y

# 2. 构建 Tauri binary（仅需要 Rust 工具链）
cd voiceflow-ui && pnpm build && pnpm tauri build

# 3. 用 Python 3.12 安装依赖到打包目录
BUNDLED_LIBS="build/deb-package/voiceflow_0.1.0_amd64/opt/voiceflow/python-libs"
/home/sikm/Tool/miniconda3/envs/build-py312/bin/pip install \
    --target="$BUNDLED_LIBS" \
    python-xlib sounddevice==0.5.1 numpy==2.2.6 \
    requests==2.32.3 pyyaml==6.0.2

# 4. 打包 deb
dpkg-deb --build build/deb-package/voiceflow_0.1.0_amd64
```

### 验证 NumPy 编译目标

```bash
# 检查 .so 文件是否为 cp312（不是 cp313）
ls /opt/voiceflow/python-libs/numpy/_core/*.so | head -1
# 应显示: ...cpython-312-x86_64-linux-gnu.so

# 测试导入
/usr/bin/python3 -c "import numpy; print(numpy.__version__)"
```

### 安装与升级

```bash
# 安装
sudo dpkg -i voiceflow_0.1.0_amd64.deb

# 升级（自动覆盖 /opt/voiceflow/）
sudo dpkg -i voiceflow_0.1.0_amd64.deb

# 重启服务
systemctl --user restart voiceflow
```

---

## 系统要求

- **操作系统**：Ubuntu 桌面（X11 required，不支持 Wayland）
- **Python**：>= 3.10（建议 3.12，参见打包说明）
- **GPU**：需要运行 ASR 模型（如 RTX 4070 及以上）
- **ASR 模型**：vLLM 部署的语音识别模型（OpenAI 兼容 API）
- **系统依赖**：`xclip` `xdotool` `libportaudio2` `aplay`（可选，用于提示音）

---

## FAQ

### Q: 为什么按下快捷键后有延迟？

正常情况下不应该有延迟。VoiceFlow 的 pre-roll 机制保证按下时立即捕获。如果仍然有延迟：
1. 检查是否修改过 `PRE_ROLL_MS`（默认值 300ms）
2. 检查 ASR 服务是否响应过慢
3. 查看日志确认是否有异常

### Q: 如何修改 pre-roll 时长？

编辑 `audio_recorder.py:27` 的 `PRE_ROLL_MS = 300`，然后重新打包安装。

### Q: 为什么修改源码后重启服务没生效？

systemd 服务运行的是**安装时打包的代码**（`/opt/voiceflow/`），不是你的开发目录。需要重新打包或直接覆盖已安装的文件。

### Q: F10 和 F11 的区别是什么？

- **F10**：智能模式，自动检测当前窗口是终端还是普通应用，选择对应快捷键
- **F11**：强制终端模式，绕过窗口检测，直接使用 `ctrl+shift+v`

### Q: 提示音不响怎么办？

检查 `beeps/` 目录是否存在 `start.wav` `end.wav` `error.wav`，以及 `aplay` 是否可用：

```bash
which aplay
ls beeps/
```

### Q: 如何查看日志？

```bash
tail -f logs/voice_input.log
```

---

## 许可证

MIT