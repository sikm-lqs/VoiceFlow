# Bug 修复记录

## Bug #1: Python 版本不匹配导致 NumPy 加载失败

### 问题现象

- VoiceFlow 服务崩溃循环，日志报错：
  ```
  ImportError: Error importing numpy: IMPORTANT: PLEASE READ THIS FOR ADVICE...
  ModuleNotFoundError: No module named 'numpy._core._multiarray_umath'
  ```
- systemd 服务状态显示 `active (running)`，但 Python 后端已崩溃
- 热键注册后 Tauri 前端仍可显示窗口，但语音输入功能完全失效

### 问题原因

| 环节 | 版本 | 说明 |
|------|------|------|
| 打包时使用的 Python | **3.13** | conda 环境 build-py312 或项目 .venv 使用 Python 3.13 |
| 打包时安装的 NumPy | **cp313** | `numpy-2.2.6-cp312-cp312...` → 实际是 cp313 的 .so 文件 |
| 运行时使用的 Python | **3.12** | `/usr/bin/python3` 是系统 Python 3.12.3 |
| 结果 | **不兼容** | Python 3.12 无法加载为 Python 3.13 编译的 .so 文件 |

```
# .so 文件后缀对比
cpython-313-x86_64-linux-gnu.so  ← 打包时（Python 3.13）
cpython-312-x86_64-linux-gnu.so  ← 运行时（Python 3.12）
```

### 影响范围

- 所有通过 `build-deb.sh` 打包的 .deb 安装包
- 受影响系统：Ubuntu 24.04（默认 Python 3.12）

### 修复方案

#### 方案 A：使用 Python 3.12 环境打包（已实施）

**步骤 1：创建 Python 3.12 虚拟环境**
```bash
# 使用 conda 创建 Python 3.12 环境
conda create -n build-py312 python=3.12 -y
conda activate build-py312
```

**步骤 2：安装依赖到打包目录**
```bash
/home/sikm/Tool/miniconda3/envs/build-py312/bin/pip install \
    --target=/path/to/build/deb-package/voiceflow_0.1.0_amd64/opt/voiceflow/python-libs \
    python-xlib sounddevice==0.5.1 numpy==2.2.6 \
    requests==2.32.3 pyyaml==6.0.2 scipy
```

**步骤 3：修改 build-deb.sh 中的 pip 命令**
```bash
# 将第 70 行：
pip3 install --target="$BUNDLED_LIBS" ...

# 改为：
/home/sikm/Tool/miniconda3/envs/build-py312/bin/pip install --target="$BUNDLED_LIBS" ...
```

#### 方案 B：修改依赖声明支持 Python 3.13（备选）

如果目标系统用户有 Python 3.13 环境，可以在 deb control 中声明：
```
Depends: python3.13, xclip, xdotool, libportaudio2
```

### 验证方法

```bash
# 检查 numpy .so 文件是否为 cp312 版本
ls /opt/voiceflow/python-libs/numpy/_core/*.so | head -1
# 应显示：...cpython-312-x86_64-linux-gnu.so

# 测试导入
/usr/bin/python3 -c "import numpy; print(numpy.__version__)"
# 应输出：2.2.6
```

### 预防措施

1. **在 build-deb.sh 中明确指定 Python 3.12 环境路径**
2. **添加验证步骤**：打包后检查 .so 文件后缀是否为 cp312
3. **文档记录**：在 CLAUDE.md 中标注打包环境要求

### 相关文件

- `/scripts/build-deb.sh` - 打包脚本（已修改）
- `/voiceflow_py/daemon.py` - Python 入口（无需修改）
- `/opt/voiceflow/python-libs/` - 运行时依赖目录

---

## Bug #2: GTK 后端初始化失败

### 问题现象

```
Failed to initialize gtk backend!: BoolError { message: "Failed to initialize GTK" }
```

### 原因

Tauri 应用在 Wayland/X11 混合环境下 GTK 初始化失败，可能与 DISPLAY 环境变量或 GTK 主题相关。

### 状态

此问题在后续重启后自动消失，可能是一次性问题。