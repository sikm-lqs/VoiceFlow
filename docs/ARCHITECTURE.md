# 架构与实现详解

本文是 [README](../README.md) 的深度补充，描述 VoiceFlow 的进程架构、调用流程、模块实现与音频传输设计。

## 双进程架构

```
┌──────────────────────────────────────────────────────┐
│                  voiceflow（一个 App）                │
│                                                      │
│   ┌────────────────────┐     ┌───────────────────┐  │
│   │  Tauri Rust        │     │  Python sidecar   │  │
│   │  /usr/bin/voiceflow│     │  daemon.py        │  │
│   │                    │     │                   │  │
│   │  负责：             │     │  负责：            │  │
│   │  - 全局快捷键       │     │  - 录音（sounddevice）│ │
│   │  - 目标窗口管理     │     │  - ASR 调用（HTTP） │  │
│   │  - 剪贴板与粘贴     │     │  - 窗口类型检测     │  │
│   │  - UI/悬浮窗口      │     │  - 临时 WAV 清理    │  │
│   └────────┬───────────┘     │                   │  │
│            │ stdin/stdout   │                   │  │
│            │ JSON Lines     └───────────────────┘  │
│            └───────────────────────────────────────│
└──────────────────────────────────────────────────────┘
```

Tauri Rust 二进制是 App 入口，Python sidecar 负责录音和 ASR 请求。两者通过 stdin/stdout JSON Lines 通信，剪贴板与粘贴由 Rust 侧完成。

## 调用流程与状态

```
hidden ──按下F10/F11──> recording ──松开──> transcribing ──┬─成功→ inserted ──> hidden
                                                          └─失败→ error ─────> hidden
```

1. **F10/F11 按下**：Rust 保存当前活动窗口（粘贴目标）→ sidecar 开始录音 → 悬浮窗跟随光标显示（音量条 + 计时）。
2. **松开**：sidecar 生成临时 WAV → 请求 ASR（本地优先，失败转云端）→ 悬浮窗转为"识别中"。
3. **结果**：Rust 恢复目标窗口焦点 → 写剪贴板 → 模拟粘贴 → 显示"已插入"后隐藏。
4. **60 秒上限**：sidecar 自动停止并通知 Rust（`recording_timeout`），自动转写已录内容，不会出现状态失锁。

sidecar → Rust 的消息类型：`ready` / `state` / `level`（实时音量，约 10Hz）/ `result` / `error`。

## 模块详解

### audio_recorder.py — 零延迟录音

核心设计：**pre-roll 环形缓冲区 + 持续开启的声卡 Stream**。

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
     │ 把 pre-roll 拷贝到  │
     │ _frames，继续录     │
     │                   ▼
     │               停止，合并
     │               _frames → WAV
     └───────────────────┘
```

- `pre_roll_ms`（默认 200，可经 `audio.pre_roll_ms` 配置）以**固定长度 numpy 环形缓冲区**实现，精确按采样数截取，不受 PortAudio 回调分块大小影响；修改后需重新打包。
- 录音回调中同步计算 RMS 音量（快攻击/慢释放包络），供悬浮窗渲染实时音量条。
- 达到 `max_record_seconds` 时自动停止，并标记 `auto_stopped` 保留已录音频。

### asr_client.py — ASR 客户端与本地/云端路由

- `ASRClient`：本地 vLLM，OpenAI 兼容 `/v1/audio/transcriptions`，multipart 上传 16kHz mono int16 WAV；异常分为 `ASRTimeoutError` / `ASRUnavailableError` / `ASREmptyError`。
- `CloudASRClient`：DashScope `qwen3-asr-flash`，WAV 以 base64 data URI 内嵌 JSON 直传公共域名。
- `ASRRouter`：本地优先；本地**不可达/超时/5xx** 时转云端；本地正常但空文本视为"未检测到语音"，不触发兜底。fallback 启用时本地重试关闭，回环连接拒绝毫秒级完成切换判定。
- 结果携带 `backend` 字段（`local` / `cloud`），日志与悬浮结果均标注来源。

### window_detector.py — 智能窗口检测

`PasteModeDetector` 两步判断当前窗口类型：

1. `xprop WM_CLASS` 获取窗口类名
2. `xdotool getwindowname` 获取窗口标题

匹配 `terminal_classes` 或 `terminal_title_keywords` 任一即判定为终端，返回 `ctrl+shift+v`，否则 `ctrl+v`。F11 跳过检测强制终端模式。

### voiceflow-ui — Tauri/React 应用

- `src-tauri/src/lib.rs`：启动 sidecar、注册 F10/F11、处理 JSON Lines（含音量/自动停止消息）和粘贴
- `src-tauri/src/paste.rs`：保存目标窗口、剪贴板操作和模拟粘贴
- `src-tauri/src/window_pos.rs`：悬浮窗定位（光标上方居中，近顶边自动翻转）
- `src/App.tsx`：监听状态与音量事件
- `src/components/VoiceOverlay.tsx`：实心深色胶囊，录音态含实时音量条与计时（55s 起变红预警），状态色语义：录音琥珀 / 识别紫 / 成功绿 / 失败红

### voiceflow_py/daemon.py — Python sidecar

- 读取 `VOICEFLOW_CONFIG` 指定的配置
- 处理 `start_recording`、`stop_and_transcribe`、`ping` 命令
- 录音期间每 100ms 上报实时音量（`{"type":"level","level":0..1}`）
- 60 秒达到上限自动停止时通知 Rust 并自动转写已录内容
- 通过 stdout 返回 `ready`、`state`、`level`、`result` 和 `error` 事件

## 音频传输方式

把音频交给 ASR 大模型，业界主要有 4 种方式：

| # | 方式 | 原理 | 适用场景 |
|---|------|------|----------|
| 1 | HTTP multipart 上传 | 音频字节作为表单二进制直接 POST，服务端在内存中接收并解码 | 本地/局域网服务 |
| 2 | Base64 内嵌 JSON | 音频字节编码成字符串塞进 JSON 请求体（体积膨胀约 33%） | 本地音频走 HTTP API，且音频没有公网地址 |
| 3 | 公网 URL 引用 | 请求中只传一个 URL，由服务端自行下载音频 | 音频已存放在 OSS/CDN 等公网可达位置 |
| 4 | WebSocket 二进制流 | 边录音边推送 PCM 音频块，识别结果流式返回 | 实时字幕、语音助手等流式场景 |

另有两种常见变体：SDK 的 `file://` 本地路径（本质是 SDK 读文件后替你走方式 1/2/3，对服务端透明）；异步文件转写 API（提交 URL 后轮询取结果，属于方式 3 的批量版本）。

### VoiceFlow 实际使用的方式

```text
本地 vLLM（backend=local）
  方式 1：sidecar 把 /tmp 临时 WAV 以 multipart 表单
          POST 到 127.0.0.1:8000/v1/audio/transcriptions

云端 DashScope（backend=cloud，本地不可用时兜底）
  方式 2：WAV 字节 base64 编码后嵌入 JSON 请求体
          {"audio": "data:audio/wav;base64,..."}
          发往 dashscope.aliyuncs.com 公共域名
```

选型原因：

- 本地服务与 VoiceFlow 同机，multipart 直传最简单、零编码开销。
- 麦克风音频没有公网地址，无法使用方式 3；专属 MaaS 端点只接受公网 URL，因此云端走公共域名的 base64 方式。
- 按住说话是"录完再识别"的批处理模式，不需要方式 4 的流式能力。

### 磁盘与清理

- 两种方式的音频都只在内存中流转：本地 vLLM 与云端服务均不会把音频写入硬盘。
- 客户端侧每次录音产生一个临时文件 `/tmp/tmpXXXX.wav`（16kHz 单声道 int16，约 32KB/秒，60 秒上限约 1.9MB），`daemon.py` 在请求结束的 `finally` 块中立即删除，识别成功或失败都会清理。
- 理论残留风险：进程在"文件已创建、请求未完成"之间被强杀（如 `kill -9`）会留下一个孤儿文件；概率极低且单个不足 2MB，必要时可手动清理 `/tmp/tmp*.wav`。
