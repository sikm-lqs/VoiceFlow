# 语音转写轻清洗

2026-11-10：现版本为 v1.0，安装、分层配置和 systemd 行为见 [V1.0.md](V1.0.md)。
本文保留最初清洗实测数据；宿主机尚未安装或重启。

## 设计选择

链路：ASR 成功 → `TextCleaner` → 原有 result 协议 → Rust 粘贴。
不修改录音、ASR 路由、快捷键、UI 或剪贴板逻辑；result 仅增加
`cleanup: {mode, elapsed_ms, fallback_reason}`，不附带原始转写副本。

默认开发配置使用 `rules`。v1.0 通过运行时配置合并，旧文件缺少 `cleanup`
时也使用 rules。`TextCleaner()` 单独无参数调用仍保持透传。
规则使用 Python 标准库，不加载本地模型、不创建 HTTP worker，也没有新增依赖。
参考开源项目的保守词表、保护区和分层清洗思路重新实现，未集成 GPL/AGPL 源码。

## 规则能力与边界

- 默认只删具有独立边界的 `呃`、`嗯`，支持同字连续犹豫声。
- 修复删除位置邻接的逗号、句号、空格；保留段落，不做全文标点重建。
- 保护引号、反引号代码、URL、邮箱和路径，包括未闭合引用。
- 不全局替换 `就是/然后/那个/啊`；保留 `嗯我们`、`嗯？` 和单独的 `嗯。`。
- 不折叠普通重复词、不做数字格式化或语义改写；宁可漏删，不贸然误删。
- 清洗后没有有效内容则返回原文。文字规则不能完美判断语气和真实语义。

例如：`呃，今天，嗯，先测试。` → `今天，先测试。`

## 配置和开关

```yaml
cleanup:
  enabled: true
  mode: rules                 # off / rules / minimax
  fillers: ["呃", "嗯"]        # [] 表示不删词；不要盲目扩充
  minimax:
    model: MiniMax-M3         # 也支持 MiniMax-M3.1-Flash
    endpoint: https://api.minimax.cn/anthropic/v1/messages
    api_key_env: MINIMAX_API_KEY
    timeout_seconds: 1.0
    max_tokens: 2048
```

进程启动时可用 `VOICEFLOW_CLEANUP_MODE=off|rules|minimax` 覆盖配置。
改动配置需在下一次启动 sidecar 时读取；没有热重载或新 UI 开关。

API key 只从环境变量读取，不写 YAML/代码。开发终端可隐藏输入：

```bash
read -rsp 'MiniMax key: ' MINIMAX_API_KEY; echo
export MINIMAX_API_KEY
# 在同一终端启动开发进程；结束后：
unset MINIMAX_API_KEY
```

不要把真实密钥直接写在命令行。systemd 启动的生产进程不会继承另一终端
的环境变量。本次未修改 systemd 环境、生产配置或生产程序。

## 可选智能清洗

只向 MiniMax 发送转写文字，不发送音频；仍然涉及云端隐私和 API 费用。
保留一个按需创建的子进程及 HTTP Session 来复用连接。超时会终止并回收
worker，避免不可取消的线程、晚到结果和二次粘贴；下次调用重新创建。
默认总请求预算 1 秒，进程终止回收可能另需约 0.2 秒及系统调度时间，
不是实时系统的严格延迟承诺。无重试、不跟随重定向。

超时、网络异常、截断或输出保护校验失败，均回退到规则结果。
数字/否定词计数、英文术语、结构化片段及长度校验只能过滤明显错误，
不能证明语义等价。新清洗代码不记录正文、密钥或外部异常正文。

官方协议：
https://platform.minimax.cn/docs/api-reference/text-anthropic-api

本次使用 M3 的 `thinking.type=disabled`；Flash 必须思考，使用
`output_config.effort=low`，不套用 M3 的禁用参数。

## 2026-11-10 实测

本机网络，合成转写 42/201/969 字，每模型每长度 20 次，共 120 次。
最多两个模型并行、每模型串行，复用 worker/连接，无重试。
测试预算 30 秒以便测出完整耗时，**不等于产品默认 1 秒预算**。
P50 为中位数，P95 为 nearest-rank；完整耗时包含 worker/IPC，
TTFT 仅为 API 首个正文 token 时间，不能等同于可安全粘贴时间。

| 模型 | 字数 | 完整 P50 | 完整 P95 |
|---|---:|---:|---:|
| M3 | 42 | 2.187s | 2.931s |
| M3 | 201 | 1.562s | 4.066s |
| M3 | 969 | 4.654s | 7.316s |
| M3.1-Flash | 42 | 1.541s | 3.026s |
| M3.1-Flash | 201 | 2.209s | 3.862s |
| M3.1-Flash | 969 | 3.762s | 5.997s |

120/120 完成且通过启发式输出校验，不代表人工确认全部语义正确。
只有 3/120 在 1 秒内完成，0/120 在 500ms 内完成。因此对即时输入不推荐
将 AI 设为必经路径；1 秒保护意味着这批样本大多会回退，并仍付出等待。
这些是特定网络、三段文本的重复测量，不是平台 SLA 或普遍质量评估。

最终规则版每长度预热 300 次、计时 5000 次：

| 字数 | P50 | P95 |
|---|---:|---:|
| 42 | 0.0056ms | 0.0063ms |
| 201 | 0.0220ms | 0.0261ms |
| 969 | 0.1336ms | 0.1574ms |

规则计时为清洗函数本身，不包括录音、ASR、粘贴；不宣称完成整机内存测评。
原始数据位于研究目录：
`/home/sikm/Project/AI-Project/My-Speech/research/voice-cleanup-2026-11-10/`，
文件为 `minimax-benchmark.json`、`rules-benchmark-final.json`。

复现（从项目目录运行；使用合成文本，不读取历史录音）：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/benchmark_cleanup.py \
  --api-key-prompt --repeats 20 --timeout 30 --output /tmp/minimax-benchmark.json
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/benchmark_cleanup.py \
  --rules-only --output /tmp/rules-benchmark.json
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

## 验证与交付边界

- v1.0 总计 128 项 Python 测试通过，涵盖保守规则、协议、超时杀进程、连接复用、
  密钥泄漏防护、智能失败回退和 ASR→清洗→result 集成。
- 系统 Python 3.12 临时 staging 中验证模块、配置和 daemon 导入；
  更新 setuptools 和 deb 脚本以携带两个新模块，脚本通过语法检查。
- 已构建完整 Tauri/deb 包；未安装、未进行真实麦克风→桌面粘贴验收。
- `/opt/voiceflow` 和 `/usr/bin/voiceflow` 校验哈希不变，生产进程未重启。
- 无自动 commit/push，保留用户原有录音、UI、Rust 和 daemon 工作区改动。
