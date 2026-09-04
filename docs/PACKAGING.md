# 构建与打包指南

本文描述如何从源码构建 VoiceFlow 的 Debian 安装包。

## 包结构

```
voiceflow_0.1.0_amd64.deb
├── DEBIAN/
│   ├── control
│   ├── postinst          # 安装后脚本（配置就绪提示、daemon-reload）
│   └── prerm             # 卸载前脚本（停止/禁用服务）
├── usr/bin/voiceflow     # Tauri Rust 二进制
├── usr/lib/systemd/user/voiceflow.service
└── opt/voiceflow/
    ├── audio_recorder.py  # sidecar 依赖
    ├── asr_client.py
    ├── window_detector.py
    ├── utils.py
    ├── config.yaml(.example)
    ├── voiceflow_py/
    │   └── daemon.py      # Python sidecar 入口
    └── python-libs/       # 打包的 Python 依赖（cp312）
```

## 一键构建

```bash
bash scripts/build-deb.sh
# 产物：build/deb-package/voiceflow_0.1.0_amd64.deb
```

脚本依次执行：`pnpm build` + `pnpm tauri build` → 组装目录结构 → 用 Python 3.12 打包 Python 依赖 → 生成 control/postinst/prerm → `dpkg-deb --build`。

## 手动构建

**重要**：打包 Python 依赖必须使用 **Python 3.12**。NumPy C 扩展（`.so`）按解释器 ABI 编译，若用 Python 3.13 打包，运行时系统 Python 3.12 会报 `ModuleNotFoundError: No module named 'numpy._core._multiarray_umath'`。

```bash
# 1. 构建 Tauri 二进制
cd voiceflow-ui && pnpm install && pnpm build && pnpm tauri build

# 2. 用 Python 3.12 安装依赖到打包目录
BUNDLED_LIBS="build/deb-package/voiceflow_0.1.0_amd64/opt/voiceflow/python-libs"
conda create -n build-py312 python=3.12 -y   # 若不存在
/path/to/miniconda3/envs/build-py312/bin/pip install --target="$BUNDLED_LIBS" \
    'python-xlib>=0.33' sounddevice==0.5.1 numpy==2.2.6 \
    requests==2.32.3 pyyaml==6.0.2 'scipy>=1.14'

# 3. 打包
dpkg-deb --build build/deb-package/voiceflow_0.1.0_amd64
```

## 验证 NumPy 编译目标

```bash
# .so 应为 cp312（不是 cp313）
ls build/deb-package/voiceflow_0.1.0_amd64/opt/voiceflow/python-libs/numpy/_core/*.so | head -1

# 安装后验证
ls /opt/voiceflow/python-libs/numpy/_core/*.so | head -1
/usr/bin/python3 -c "import numpy; print(numpy.__version__)"
```

## 安装与升级

```bash
sudo dpkg -i voiceflow_0.1.0_amd64.deb
systemctl --user daemon-reload
systemctl --user restart voiceflow.service
```

注意：安装不会覆盖已存在的 `/opt/voiceflow/config.yaml`（首次安装会从 `config.yaml.example` 生成），升级后如需应用新的配置项请手动合并。
