# 配置参考

v1.0 使用分层配置：`/opt/voiceflow/defaults.yaml` → `/etc/voiceflow/config.yaml`
→ `~/.config/voiceflow/config.yaml` → `VOICEFLOW_CONFIG` 指定的覆盖文件。
机器配置不存在时才读取旧 `/opt/voiceflow/config.yaml`。安装器自动迁移，
升级不覆盖个人配置。映射递归合并，列表替换；详细说明见 [V1.0.md](V1.0.md)。

## 完整示例

新增可选 `cleanup` 配置见 [CLEANUP.md](CLEANUP.md)。开发默认 `rules`，
v1.0 旧配置不含该节时继承 rules 默认值；显式 disabled/off 保留。
`VOICEFLOW_CLEANUP_MODE` 可在启动时覆盖。
MiniMax 密钥仅从环境变量读取，不写入配置文件。

```yaml
hotkey:
  smart_combo: f10        # 智能粘贴模式
  terminal_combo: f11     # 终端粘贴模式

audio:
  sample_rate: 16000      # 采样率
  channels: 1             # 声道数
  dtype: int16            # 数据格式
  max_record_seconds: 60  # 单次最长录音时间
  pre_roll_ms: 200        # 按键前预录时长（精确按采样数截取，0 为关闭）

asr:
  endpoint: http://127.0.0.1:8000     # 本地 vLLM 服务地址
  model: /path/to/qwen3-asr-1.7B      # 模型路径或名称
  timeout_seconds: 20                 # 本地请求超时
  language: zh                        # 语言
  prompt: ""                          # 可选 hint prompt
  fallback:                           # 本地不可用时转云端
    enabled: true
    endpoint: https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
    model: qwen3-asr-flash            # 支持 base64 直传本地音频
    api_key_env: DASHSCOPE_API_KEY    # 密钥从环境变量读取，不要写入本文件
    timeout_seconds: 20
    enable_itn: true

paste:
  enabled: true
  smart_mode: true                    # 智能窗口检测
  default_shortcut: ctrl+v            # 普通窗口粘贴快捷键
  terminal_shortcut: ctrl+shift+v     # 终端窗口粘贴快捷键
  restore_clipboard: true             # 是否恢复原剪贴板

  terminal_classes:                   # 终端窗口类名（xprop WM_CLASS）
    - gnome-terminal
    - konsole
    - xfce4-terminal
    - kitty
    - alacritty
    - wezterm
    - tilix
    - xterm
    - org.gnome.terminal

  terminal_title_keywords:            # 终端标题关键词兜底
    - terminal
    - 终端
    - bash
    - zsh
    - fish
    - ssh
    - tmux
    - vim
    - nvim

ux:
  indicator: true
  indicator_follow_pointer: true
  start_beep: true
  end_beep: true
  error_beep: true
  notify: true

log:
  level: INFO
  file: ./logs/voice_input.log
```

## 字段生效情况

| 字段 | 状态 | 说明 |
|---|---|---|
| `audio.sample_rate` / `channels` / `max_record_seconds` | 生效 | 传给录音器 |
| `audio.pre_roll_ms` | 生效 | 按键前预录时长，精确按采样数截取；0 为关闭 |
| `audio.dtype` | 固定 int16 | 配置项预留 |
| `asr.*`（含 `fallback`） | 生效 | 本地请求与云端兜底 |
| `paste.smart_mode` / `default_shortcut` / `terminal_shortcut` / `terminal_classes` / `terminal_title_keywords` | 生效 | 窗口检测与快捷键选择 |
| `paste.enabled` / `restore_clipboard` | 固定开启 | 当前由 Rust 侧硬编码 |
| `hotkey.*` | 预留 | 快捷键当前由 Rust 端固定为 F10/F11 |
| `ux.*` / `log.*` | 预留 | 尚未接入 Tauri 主链路；日志由 systemd 收集 |

## 云端兜底密钥注入

`api_key_env` 指定的环境变量由 systemd 用户级 drop-in 注入，密钥不落入仓库：

```bash
# ~/.config/systemd/user/voiceflow.service.d/asr-fallback.conf
[Service]
Environment=DASHSCOPE_API_KEY=sk-xxx
```

```bash
chmod 600 ~/.config/systemd/user/voiceflow.service.d/asr-fallback.conf
systemctl --user daemon-reload
systemctl --user restart voiceflow.service
```

## 配置验证

启动时 `utils.load_config()` 自动校验：缺少必填字段（`hotkey` / `audio` / `asr` / `paste` 及其关键子字段）或类型错误会抛出 `ConfigValidationError`，sidecar 退出并在 Rust 侧显示错误状态。
