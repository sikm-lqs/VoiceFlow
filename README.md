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
- **零启动延迟录音** — 声卡 Stream 始终保持预热，加上 200ms pre-roll 环形缓冲区，捕获说话的第一毫秒
- **自动粘贴** — 识别结果直接插入当前光标位置（通过剪贴板模拟粘贴）
- **智能窗口检测** — 自动判断当前窗口类型，选择正确的粘贴快捷键
- **纯本机运行** — 音频不走外网，隐私安全
- **低延迟** — 录音到出字端到端 1-3 秒
- **悬浮指示器** — mac 风格的录音/识别状态胶囊
- **状态提示** — 录音、识别和插入状态通过悬浮窗口反馈
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
  --host 127.0.0.1 --port 8000
```

### 3. 安装与运行

```bash
cd voiceflow-ui
pnpm install
pnpm tauri dev
```

生产环境使用打包后的 `/usr/bin/voiceflow`，Python sidecar 和配置位于 `/opt/voiceflow/`。

按住 **F10**（智能粘贴）或 **F11**（终端粘贴）开始录音，松开后自动识别并粘贴。

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
│   │  负责：            │     │  负责：             │  │
│   │  - 全局快捷键      │     │  - 录音（sounddevice）│  │
│   │  - 目标窗口管理    │     │  - ASR 调用（HTTP）  │  │
│   │  - 剪贴板与粘贴    │     │  - 窗口类型检测      │  │
│   │  - UI/悬浮窗口     │     │  - 临时 WAV 清理     │  │
│   └────────┬───────────┘     │                     │  │
│            │ stdin/stdout   │                     │  │
│            │ JSON Lines     └─────────────────────┘  │
│            └───────────────────────────────────────│
└──────────────────────────────────────────────────────┘
```

Tauri Rust 二进制（`/usr/bin/voiceflow`）是 App 入口，Python sidecar（`daemon.py`）负责录音和 ASR 请求。两者通过 stdin/stdout JSON Lines 通信。剪贴板与粘贴由 Rust 侧完成。

### 当前调用流程

```
F10/F11 按下 → Rust 保存目标窗口 → sidecar 开始录音
F10/F11 松开 → sidecar 生成 WAV → 请求 vLLM ASR
ASR 返回文本 → Rust 恢复目标窗口 → 剪贴板粘贴
```

### 运行时线程

Rust 主进程负责桌面集成和粘贴，Python sidecar 负责录音、ASR 请求及窗口类型检测。两者之间使用逐行 JSON 消息通信。
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
  fallback:                           # 本地不可用时转云端
    enabled: true
    endpoint: https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
    model: qwen3-asr-flash            # 支持 base64 直传本地音频
    api_key_env: DASHSCOPE_API_KEY    # 密钥从环境变量读取，不写入本文件
    timeout_seconds: 20
    enable_itn: true

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
持续写入 pre-roll deque（最近200ms）  │
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

`PRE_ROLL_MS = 200` 是硬编码常量，在 [audio_recorder.py:27](audio_recorder.py#L27)。如需调整，直接修改此值后重新打包。

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

### voiceflow-ui — Tauri/React 应用

- `src-tauri/src/lib.rs`：启动 sidecar、注册 F10/F11、处理 JSON Lines 和粘贴
- `src-tauri/src/paste.rs`：保存目标窗口、剪贴板操作和模拟粘贴
- `src-tauri/src/window_pos.rs`：悬浮窗口定位
- `src/App.tsx`：监听状态事件并渲染界面

### voiceflow_py/daemon.py — Python sidecar

- 读取 `VOICEFLOW_CONFIG` 指定的配置
- 处理 `start_recording`、`stop_and_transcribe`、`ping` 命令
- 使用共享的录音器、ASR 客户端和窗口检测模块
- 通过 stdout 返回 `ready`、`state`、`result` 和 `error` 事件

---

## 项目结构

```
voiceflow/
├── audio_recorder.py       # 录音（pre-roll + 持续声卡）
├── asr_client.py           # ASR HTTP 客户端
├── window_detector.py      # 智能窗口检测（xprop/xdotool）
├── utils.py               # 配置加载 & 日志
├── config.yaml            # 配置文件
├── pyproject.toml         # 依赖管理（uv）
├── CLAUDE.md              # Claude Code 指南
├── tests/                 # 单元测试
│   ├── test_asr_client_mock.py
│   └── test_utils.py
├── voiceflow_py/          # Python sidecar
│   └── daemon.py
├── voiceflow-ui/          # Tauri 前端（React）
│   ├── src/
│   └── src-tauri/
├── scripts/               # 辅助脚本
│   ├── build-deb.sh
│   ├── check_audio.sh
│   └── packaging/voiceflow.service
```

---

## 开发

### 环境搭建与开发运行

```bash
# Python sidecar 依赖
uv venv
uv pip install -e ".[dev]"

# Tauri/React 开发模式
cd voiceflow-ui
pnpm install
pnpm tauri dev
```

### 日志

生产安装的 VoiceFlow 日志由用户级 systemd 收集。

查看实时日志：

```bash
journalctl --user-unit=voiceflow.service -f
```

### 修改预录缓冲区时间

`audio_recorder.py:27` 的 `PRE_ROLL_MS = 200` 控制 pre-roll 时长。修改后需重新打包安装：

```bash
# 重新打包并安装（正式发布）
# 见“打包与安装”章节
```

---

## 测试

```bash
# 运行所有测试
uv run pytest tests/ -v

# 单个测试文件
uv run pytest tests/test_asr_client_mock.py -v

# 覆盖率
uv run pytest tests/ --cov=. --cov-report=term-missing
```

### 测试内容

| 文件 | 覆盖范围 |
|------|----------|
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
    ├── audio_recorder.py  # sidecar 依赖
    ├── asr_client.py
    ├── window_detector.py
    ├── utils.py
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
1. 检查是否修改过 `PRE_ROLL_MS`（默认值 200ms）
2. 检查 ASR 服务是否响应过慢
3. 查看日志确认是否有异常

### Q: 如何修改 pre-roll 时长？

编辑 `audio_recorder.py:27` 的 `PRE_ROLL_MS = 200`，然后重新打包安装。

### Q: 本地 ASR 停了怎么办？

`asr.fallback.enabled: true` 时自动切换云端 `qwen3-asr-flash`。本地服务进程停止时回环连接立即失败，fallback 判定开销接近 0；总延迟约等于云端调用（典型 2-4 秒）。本地正常但识别为空文本时不会走云端（视为"未检测到语音"）。密钥通过 systemd drop-in 环境变量 `DASHSCOPE_API_KEY` 注入，日志会标注 `backend=local/cloud`。

### Q: 为什么修改源码后重启服务没生效？

systemd 服务运行的是**安装时打包的代码**（`/opt/voiceflow/`），不是你的开发目录。需要重新打包或直接覆盖已安装的文件。

### Q: F10 和 F11 的区别是什么？

- **F10**：智能模式，自动检测当前窗口是终端还是普通应用，选择对应快捷键
- **F11**：强制终端模式，绕过窗口检测，直接使用 `ctrl+shift+v`

### Q: 如何查看日志？

```bash
journalctl --user-unit=voiceflow.service -f
```

---

## 许可证

MIT
