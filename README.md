# VoiceFlow

## v1.0：单命令安装

在 Ubuntu 24.04 amd64 / X11 桌面终端执行：

```bash
sudo dpkg -i /home/sikm/Project/AI-Project/TTS/build/deb-package/voiceflow_1.0.0_amd64.deb
```

自动备份、迁移旧配置，配置当前用户的 systemd 服务，并恢复原运行状态。
默认规则清洗、MiniMax 关闭；已主动关闭自启的用户不会被升级擅自重新开启。
首次从 0.1.x 升级请避开正在录音/识别的时刻。
完整[安装、架构、配置与使用说明](docs/V1.0.md)。

![Platform](https://img.shields.io/badge/platform-Ubuntu%20X11-blue)
![Sidecar](https://img.shields.io/badge/sidecar-Python%203.12-informational)
![App](https://img.shields.io/badge/app-Tauri%202%20%C2%B7%20React-24C8DB)
![ASR](https://img.shields.io/badge/ASR-Qwen3--ASR--1.7B%20%2B%20云端兜底-6E56CF)
![License](https://img.shields.io/badge/license-MIT-green)

**本地语音输入助手** — 按住 F10 说话，松开后自动识别并粘贴到光标位置。无需切换应用，说完即出字。

优先使用本地 vLLM 部署的 Qwen3-ASR 模型；本地服务不可用时自动切换云端 API 兜底，全程无需人工干预。

[English](README_EN.md)

## 特性

- **双热键模式** — F10 智能粘贴（自动检测终端/普通窗口），F11 强制终端粘贴
- **零启动延迟** — 声卡 Stream 常驻预热 + 200ms pre-roll 环形缓冲，捕获第一个字
- **本地优先，云端兜底** — 本地 vLLM 不可用时毫秒级切换 `qwen3-asr-flash`，日志标注 `backend=local/cloud`
- **实时悬浮反馈** — 深色胶囊悬浮于光标上方：录音时显示实时音量条与计时，识别/插入/失败状态一目了然
- **自动粘贴** — 识别结果经剪贴板直接插入光标位置，并自动恢复原剪贴板
- **轻清洗（开发源码）** — 保守删除独立犹豫声，局部修复标点；可选 MiniMax 智能清洗及超时回退。配置、边界与实测见 [docs/CLEANUP.md](docs/CLEANUP.md)
- **纯本机音频** — 音频仅在内存与本机回环地址流转，不上传外网（云端兜底除外）

## 安装

### 1. 系统依赖

```bash
sudo apt install xclip xdotool libportaudio2
```

### 2. 启动本地 ASR 服务

```bash
vllm serve /path/to/qwen3-asr-1.7B --host 127.0.0.1 --port 8000
```

### 3. 安装 VoiceFlow

从源码构建 deb 包并安装（详见 [docs/PACKAGING.md](docs/PACKAGING.md)）：

```bash
bash scripts/build-deb.sh
sudo dpkg -i build/deb-package/voiceflow_1.0.0_amd64.deb
```

如需云端兜底，将 DashScope API Key 注入 systemd 环境变量（详见 [docs/CONFIGURATION.md](docs/CONFIGURATION.md)）。
v1.0 已自动处理旧配置迁移和服务恢复；不再需要 0.1.1 的手工配置合并链路。
默认只启用规则清洗，不调用 MiniMax。无图形会话时登录后再启动。

## 使用

| 热键 | 窗口检测 | 粘贴快捷键 | 适用场景 |
|------|----------|-----------|----------|
| F10 | 自动智能检测 | `ctrl+v` 或 `ctrl+shift+v` | 大多数场景 |
| F11 | 跳过检测 | `ctrl+shift+v`（强制） | 智能检测失败时 |

```
聚焦输入框 → 按住 F10 → 说话 → 松开 → 文字自动出现在光标处
```

录音悬浮窗会实时显示音量条与计时；松开后依次显示"识别中 → 已插入"。单次录音上限 60 秒，达到上限自动转写。

## 工作原理

```text
systemd user service
  └─ Tauri/Rust 应用（快捷键 / 悬浮窗 / 目标窗口 / 剪贴板粘贴）
      └─ Python sidecar（录音 / ASR 请求 / 窗口类型检测）
          ├─ 本地优先：POST 127.0.0.1:8000/v1/audio/transcriptions（multipart）
          └─ 云端兜底：DashScope qwen3-asr-flash（base64 直传）
```

Rust 与 Python 之间通过 stdin/stdout JSON Lines 通信（状态、实时音量、结果）。识别错误自动分类并重试，任何阶段失败都不崩溃。

更多细节见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)（模块实现、状态机、音频传输方式对比、临时文件生命周期）。

## 配置

运行时行为由 `/opt/voiceflow/config.yaml` 控制：ASR 端点与模型、云端兜底开关、终端窗口检测规则、录音参数等。完整字段说明与生效情况见 [docs/CONFIGURATION.md](docs/CONFIGURATION.md)。

核心片段：

```yaml
asr:
  endpoint: http://127.0.0.1:8000      # 本地 vLLM
  fallback:                            # 本地不可用时兜底
    enabled: true
    model: qwen3-asr-flash
    api_key_env: DASHSCOPE_API_KEY     # 密钥走环境变量，不写入本文件
```

## 开发

```bash
# Python sidecar 依赖与测试
uv venv && uv pip install -e ".[dev]"
uv run pytest tests/ -v

# Tauri/React 开发模式
cd voiceflow-ui && pnpm install && pnpm tauri dev

# 构建安装包
bash scripts/build-deb.sh
```

查看服务日志：

```bash
journalctl --user-unit=voiceflow.service -f
```

## FAQ

**Q: 本地 ASR 停了怎么办？**

自动切换云端兜底。本地服务停止时回环连接立即失败，切换判定开销接近 0，总延迟约等于云端调用。日志中 `backend=cloud` 表示本次走了云端。

**Q: 为什么修改源码后重启服务没生效？**

systemd 服务运行的是安装时打包的代码（`/opt/voiceflow/`），不是开发目录，需要重新打包安装。

**Q: 按下快捷键后有延迟？**

pre-roll 机制保证按下即捕获，正常无延迟。若有，检查 `PRE_ROLL_MS` 是否被改动、ASR 服务响应、以及 `journalctl` 日志。

**Q: 支持 Wayland 吗？**

不支持。全局热键与模拟粘贴依赖 X11（`xdotool`/`xprop`）。

## 文档

- [架构与实现详解](docs/ARCHITECTURE.md) — 进程模型、模块实现、音频传输方式、临时文件生命周期
- [配置参考](docs/CONFIGURATION.md) — 完整字段说明、生效情况、云端密钥注入
- [构建与打包指南](docs/PACKAGING.md) — deb 构建、Python 3.12 ABI 注意事项

## 系统要求

- Ubuntu 桌面（X11，不支持 Wayland）
- 本地 ASR：vLLM 部署的 OpenAI 兼容语音识别模型 + NVIDIA GPU
- 可选云端兜底：DashScope API Key

## 许可证

MIT
