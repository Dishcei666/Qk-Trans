# Qk-Trans · 本地离线翻译系统

便捷的中英互译工具:本地运行、完全离线、数据不出电脑。

- **双引擎可切换**
  - 轻量快速:OPUS-MT 专用翻译模型(CTranslate2 int8 量化),CPU 即可流畅运行,响应通常在几十毫秒
  - AI 大模型:复用本机 [Ollama](https://ollama.com),译文更自然,可选装
- **文本翻译**:输入停顿后自动翻译,Ctrl+Enter 立即翻译;自动方向检测;复制、历史记录
- **文件翻译**:拖入 txt / md / pdf / docx / srt / vtt 整篇翻译,支持"中英对照"输出;字幕保留时间轴,Word 保留段落结构
- **隐私**:所有翻译在本机完成,断网可用

## 首次安装(需要联网,仅此一次)

1. 确认已安装 [Python 3.10+](https://www.python.org/downloads/)(安装时勾选 *Add to PATH*)
2. 双击 `setup.bat`,等待完成:创建虚拟环境 → 安装依赖 → 下载并转换模型(约 700 MB)
   - 模型下载默认自动使用 hf-mirror.com 镜像;也可用环境变量 `HF_ENDPOINT` 指定其他端点
   - pip 下载慢时,可在 `setup.bat` 中给 `pip install` 加上 `-i https://pypi.tuna.tsinghua.edu.cn/simple`

## 日常使用

双击 `start.bat`,浏览器会自动打开 `http://127.0.0.1:8477`(保持黑窗口开着即可,关掉即退出)。

| 操作 | 说明 |
|---|---|
| 输入文字 | 停顿 0.6 秒后自动翻译 |
| Ctrl + Enter | 立即翻译 |
| 轻量快速 / AI 大模型 | 切换引擎 |
| 自动 / 中→英 / 英→中 | 切换方向,自动模式会显示检测结果 |
| 😘 历史 | 查看最近 50 条翻译(存在浏览器本地) |
| 📄 文件翻译 | 切到文件标签页,拖入或选择文件;勾选"中英对照"可输出段对段对照文件 |

文件翻译说明:翻译完成后**译文直接在页面内逐段对照预览**(灰色原文 + 译文,可折叠),同时保留下载按钮;产物保存在 `files/translations/`(可随时手动清理);PDF 输出为纯文本(扫描版 PDF 无法提取文字);Word 的表格内容暂不翻译;文件上限 20MB / 40 万字符,超长文档页面预览显示前 2000 段、文件完整。

## 启用 AI 大模型引擎(可选)

1. 安装 [Ollama for Windows](https://ollama.com/download)
2. 拉取默认模型(约 4.7 GB,按显存可换更小的):
   ```
   ollama pull qwen2.5:7b-instruct
   ```
3. 重启 `start.bat`,顶栏状态会显示"大模型就绪"

想换模型时,修改 `config.json` 里的 `ollama_model` 即可(例如显存不足用 `qwen2.5:3b-instruct`)。

## 配置说明(config.json)

| 键 | 默认值 | 说明 |
|---|---|---|
| `host` / `port` | `127.0.0.1` / `8477` | 服务监听地址,改端口后重启生效 |
| `open_browser` | `true` | 启动时是否自动打开浏览器 |
| `ct2_device` | `auto` | 轻量引擎设备:`auto` / `cpu` / `cuda`;`auto` 检测到可用 GPU 则用 GPU,缺少 cuDNN 时自动回退 CPU |
| `ollama_base_url` | `http://127.0.0.1:11434` | Ollama 服务地址 |
| `ollama_model` | `qwen2.5:7b-instruct` | LLM 引擎使用的模型 |
| `ollama_timeout` | `120` | LLM 引擎超时(秒) |

## 常见问题

- **端口被占用**:改 `config.json` 的 `port` 后重启
- **轻量引擎提示模型未就绪**:重新运行 `setup.bat`(已下载的部分会自动跳过)
- **大模型按钮置灰**:Ollama 未启动或模型未拉取,看页面底部状态栏提示的具体原因
- **控制台提示"轻量引擎改用 CPU"**:说明 GPU 推理栈不可用(常见于缺少 CUDA 运行库,如 `cublas64_12.dll`,或个别新架构显卡驱动不兼容),系统已自动回退 CPU。CPU int8 模式延迟通常也只有几十毫秒;如需启用 GPU,可安装 [CUDA 12 运行库](https://developer.nvidia.com/cuda-downloads) 后把 `ct2_device` 设为 `"cuda"` 重试
- **防火墙弹窗**:首次启动 Windows 可能询问,允许"专用网络"即可;服务只监听本机回环地址,外部无法访问

## 目录结构

```
Qk-Trans/
├── app/                  # FastAPI 后端(引擎抽象、语言检测、API)
├── web/                  # 前端页面(无框架、无 CDN,纯离线)
├── scripts/              # 模型下载与转换脚本
├── models/               # 翻译模型(setup 时生成,opus-mt-zh-en / opus-mt-en-zh)
├── setup.bat             # 首次安装(联网,一次性)
├── start.bat             # 日常启动
└── config.json           # 配置
```
