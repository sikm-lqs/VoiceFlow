# 0.1.1：安装、systemd 与阿里云 ASR

2026-11-10；此文中的安装命令由用户自行执行，本次开发未安装或重启现用服务。

## 本机检查结果

- 当前 dpkg 版本：0.1.0。
- `voiceflow.service` 是 **用户级**服务，当前 `enabled`、`active`。
- 主单元：`/usr/lib/systemd/user/voiceflow.service`，启动 `/usr/bin/voiceflow`。
- 程序根目录：`/opt/voiceflow`，配置：`/opt/voiceflow/config.yaml`。
- 本机覆盖：
  - `/home/sikm/.config/systemd/user/voiceflow.service.d/override.conf`
  - `/home/sikm/.config/systemd/user/voiceflow.service.d/asr-fallback.conf`
- 前者设置显示环境，后者设置 DashScope 密钥。未展示、复制到仓库或修改密钥。
- `Linger=yes`。服务包含等待 X 会话就绪的前置检查；这是图形桌面输入工具，
  不是未登录图形桌面时也能完成粘贴的后台接口。

用户级 `systemctl --user ...` **不要加 sudo**；只有安装和写 `/opt` 需要 sudo。
不要另外手动启动第二个 `/usr/bin/voiceflow`。

## 默认状态

本次 Git 提交只纳入轻清洗、发布版本、打包安全修正和文档。
完整包按当前工作区构建，保留本来已有、尚未提交的录音和界面修复，
没有把这些改动擅自提交或回退。因此安装包不是纯净 Git 提交的逐字构建。
同目录的 `voiceflow_0.1.1_source.tar.gz` 与
`voiceflow_0.1.1_build-info.json` 记录实际源码快照、文件哈希和提交来源，
便于重现这个包。生成的 pycache 和无关未跟踪文件不纳入源码快照。

新包配置：`cleanup.enabled: true`、`cleanup.mode: rules`。
**MiniMax 不会默认调用**；嵌套的模型/endpoint 仅为可选配置。
旧配置没有 cleanup 段时清洗仍关闭，因此升级保留原配置之后必须合并该段。
启动环境 `VOICEFLOW_CLEANUP_MODE` 的优先级高于 YAML；本次检查到的本机
drop-in 没有设置此覆盖。下方合并显式选择 rules，不修改 ASR 配置或密钥。

## 按顺序安装（同一个桌面终端）

### 1. 私密备份

```bash
PKG=/home/sikm/Project/AI-Project/TTS/build/deb-package/voiceflow_0.1.1_amd64.deb
BACKUP="$HOME/.local/state/voiceflow/backups/$(date +%Y%m%d-%H%M%S)"
install -d -m 700 "$BACKUP"
sudo cp -a /opt/voiceflow/config.yaml "$BACKUP/config.yaml"
cp -a "$HOME/.config/systemd/user/voiceflow.service.d" "$BACKUP/"
sudo tar -C / -czf "$BACKUP/installed-runtime.tar.gz" \
  opt/voiceflow usr/bin/voiceflow usr/lib/systemd/user/voiceflow.service
printf 'Backup directory: %s\n' "$BACKUP"
```

备份含本机配置和 API key 所在的 drop-in，不要上传或公开。
保留 `$BACKUP` 的实际路径供回退使用。备份失败则先不要继续。

### 2. 停止用户服务、安装、恢复原配置

```bash
systemctl --user stop voiceflow.service
sudo dpkg --force-confold -i "$PKG"
sudo cp -a "$BACKUP/config.yaml" /opt/voiceflow/config.yaml
```

安装失败时先解决错误，不要直接跳到重启。`--force-confold` 配合备份恢复，
避免 0.1.0 的非 conffile 向新版 conffile 过渡时丢失个性化配置。
0.1.1 起登记 `/opt/voiceflow/config.yaml` 为 Debian conffile；
新维护脚本不再主动执行用户服务的 stop/disable/restart。
首次从旧版升级时旧版的维护脚本仍可能被 dpkg 执行，下方显式 enable 自启。

### 3. 合并轻清洗配置（其余键保留）

下列命令只把 cleanup 切到本地规则，保留 ASR 参数等其他字段。
YAML 注释会在重新序列化时丢失，原文件已备份。原子替换保留权限和所有者。

```bash
sudo /usr/bin/python3 - <<'PY'
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, "/opt/voiceflow")
sys.path.insert(0, "/opt/voiceflow/python-libs")
import yaml
from utils import validate_config

p = Path("/opt/voiceflow/config.yaml")
cfg = yaml.safe_load(p.read_text())
cleanup = cfg.setdefault("cleanup", {})
cleanup.update(enabled=True, mode="rules", fillers=["呃", "嗯"])
validate_config(cfg)
st = p.stat()
fd, name = tempfile.mkstemp(prefix=".config-cleanup-", dir=p.parent)
try:
    with os.fdopen(fd, "w") as f:
        os.fchmod(f.fileno(), st.st_mode & 0o777)
        os.fchown(f.fileno(), st.st_uid, st.st_gid)
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(name, p)
finally:
    if os.path.exists(name):
        os.unlink(name)
print("Configured: cleanup.mode=rules; MiniMax disabled")
PY
```

### 4. 恢复自启、启动并验证

```bash
systemctl --user daemon-reload
systemctl --user enable voiceflow.service
systemctl --user restart voiceflow.service
systemctl --user is-enabled voiceflow.service
systemctl --user is-active voiceflow.service
dpkg-query -W -f='${Version}\n' voiceflow
journalctl --user -u voiceflow.service -n 40 --no-pager
```

预期：`enabled`、`active`、版本 `0.1.1`。不要公开完整环境变量或包含密钥的
服务配置输出；日志也应自行审查后再分享。

实际验收：编辑器中按 F10 说“呃，今天先测试。”，检查轻清洗和粘贴；
终端用 F11 测试；单独“嗯？”及引号内容应保留。
ASR 可能已先移除口水词，因此一次看不到“嗯”不代表清洗模块一定生效。
清洗模块当前没有 UI 开关或全文调试日志。

## 回退

在同一个终端（或把 `$BACKUP` 重新设为真实备份路径）执行：

```bash
systemctl --user stop voiceflow.service
sudo dpkg --force-confold -i \
  /home/sikm/Project/AI-Project/TTS/build/deb-package/voiceflow_0.1.0_amd64.deb
sudo tar -C / -xzf "$BACKUP/installed-runtime.tar.gz"
systemctl --user daemon-reload
systemctl --user enable voiceflow.service
systemctl --user restart voiceflow.service
```

旧 deb 已保留。随后恢复运行时快照，是为了连同原本地录音/界面行为一起恢复，
不假设旧安装包字节与升级前的机器状态完全相同。

## 实际 ASR 调用链

读取当前源码和 `/opt/voiceflow/config.yaml` 后确认：

```text
音频
 └─ 本地 vLLM：Qwen3-ASR-1.7B
     POST http://127.0.0.1:8000/v1/audio/transcriptions
     ├─ 成功 → 本地规则清洗 → 粘贴
     ├─ 超时/连接失败/映射为不可用的 HTTP 错误
     │   └─ 阿里云 DashScope：qwen3-asr-flash
     │       └─ 成功 → 本地规则清洗 → 粘贴
     └─ 正常响应但无语音/空结果 → 不调用云端兜底
```

不是同时请求两个模型，也不是识别后再让阿里云改写。
本地成功时不调用云端；本地出错时才有云端费用和音频上传。
启用 fallback 时本地 `max_retries=0`，不再本地重试一次才兜底。

阿里云当前 endpoint：

```text
https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
```

当前请求要点（省略真实音频、密钥）：

```json
{
  "model": "qwen3-asr-flash",
  "input": {
    "messages": [
      {"role": "user", "content": [{"audio": "data:audio/wav;base64,..."}]}
    ]
  },
  "parameters": {
    "result_format": "message",
    "asr_options": {"language": "zh", "enable_itn": true}
  }
}
```

鉴权：`Authorization: Bearer <DASHSCOPE_API_KEY>`；
源码使用非流式返回，音频以 base64 直接提交，无需公网文件 URL。
当前配置中 `asr.prompt` 只传给本地 OpenAI 兼容转写接口，
**没有传给 CloudASRClient**；本地服务是否真正采用 prompt 取决于服务实现。

## 阿里云能否用 prompt 直接轻清洗？

2026-11-10 核查官方 Qwen ASR 文档：
https://help.aliyun.com/zh/model-studio/qwen-asr-api-reference

1. **支持 system 消息，但它是 ASR 上下文，不是通用指令。**
   官方用途是背景文本、实体词表等，明确不支持传统角色类系统提示词。
   可用来帮助识别“VoiceFlow、Qwen3-ASR”等术语，不应把“删除所有口水词、
   重写句子”当成有保证的接口能力。本次没有为云端添加这种未验证指令。
2. **`enable_itn` 是逆文本标准化，不是去口水词。**
   主要把中文/英文数字表达转为阿拉伯数字等；你当前已经开启。
3. 在该模型公开参数中，**未找到文档化的去口水词开关**，也未找到可控制
   全文补标点的 `punctuation_prediction_enabled` 开关。文档中出现输出
   `punctuation` 字段不等于支持同名控制参数。
4. 不应把其他阿里云 ASR 产品（例如某些 FunASR/Paraformer 接口）的
   `disfluency_removal_enabled` 等参数直接塞给 Qwen3-ASR-Flash。
   只有目标模型对应文档明确支持、并经实测，才应接入。

结论：Qwen ASR 的自然转写可能已经处理部分标点/犹豫声，但不能据此承诺
任意 prompt 控制清洗。当前 **ASR→本地规则** 同时覆盖本地和云端结果，
无第二次网络调用，是更确定的低延迟方案。若后续做 system 上下文实验，
需用含否定、数字、语气与引用的音频做 A/B 测试，不保证零额外模型延迟。

endpoint 属于现有配置；官方页面同时提示旧域名向业务空间域名迁移。
本次不擅自更换地域/工作空间，不验证此密钥所属业务空间，也没有额外调用
阿里云计费接口来测试清洗效果。
