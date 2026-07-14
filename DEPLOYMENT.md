# ExhibitFlow Lite 可迁移部署与使用说明

这份说明对应当前的轻量链路：**任务配置 → 文案/视觉分析 → 素材选择 → Qwen/Edge TTS → FFmpeg 内部合成 → 结果回流 SaaS 页面**。发布包不依赖原 `MoneyPrinterTurbo` 源码目录，默认使用项目内的 `exhibitflow_lite`、`materials` 和 `vendor` 目录。

## 1. 发布包内容

发布包包含：

- Python API 服务：`api_server.py`
- 前端静态页面：`frontend/index.html`
- 本地素材示例：`materials/`
- 核心业务包：`exhibitflow_lite/`
- 可选社交抓取代码：`vendor/social_crawler/`
- 启动和安装脚本：`scripts/`
- 环境变量模板：`.env.example`

发布包**不包含**：`.env`、`.venv`、Git 历史、真实浏览器 Cookie、运行历史、OAuth Token、渲染缓存和日志。目的不是丢失功能，而是避免把当前电脑的密钥和账号授权复制出去。

## 2. macOS / Linux 部署

### 环境要求

- Python 3.10 或更高版本
- ffmpeg 和 ffprobe
- 能访问所选模型服务的网络
- 建议 4GB 以上内存；2GB 服务器只适合低并发、单任务运行

### 安装

```bash
tar -xzf exhibitflow-lite-portable-20260713.tar.gz
cd exhibitflow-lite
./scripts/bootstrap.sh
```

`bootstrap.sh` 会创建 `.venv`、安装核心依赖、创建运行目录，并从 `.env.example` 生成 `.env`。

### 配置模型

编辑 `.env`，至少选择一个文本模型配置：

```dotenv
# DeepSeek 文本模型
DEEPSEEK_API_KEY=你的DeepSeek_KEY
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

# Qwen 视觉 / Qwen TTS（按需）
DASHSCOPE_API_KEY=你的百炼_KEY
QWEN_VL_MODEL=qwen3-vl-plus
QWEN_TTS_MODEL=你的可用Qwen_TTS模型
```

没有填写在线模型 Key 时，仍可以启动页面并使用本地素材链路；文案、视觉理解或 Qwen TTS 请求会提示配置缺失。

### 启动

```bash
./scripts/verify-install.sh
./scripts/start-stack.sh
```

浏览器打开：

```text
http://127.0.0.1:5173/?api=http://127.0.0.1:8610
```

默认端口：

- API：`8610`
- 前端：`5173`

修改端口：

```bash
EXHIBITFLOW_PORT=9000 FRONTEND_PORT=9001 ./scripts/start-stack.sh
```

如果端口需要被局域网其他设备访问，可在 `.env` 设置 `EXHIBITFLOW_HOST=0.0.0.0`，并按实际网络情况配置防火墙。生产环境建议在反向代理后使用 HTTPS，不要直接暴露开发服务。

## 3. Windows 部署

当前发布包的 `.sh` 脚本面向 macOS/Linux。Windows 可以直接使用同一套 Python 代码：

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install .
copy .env.example .env
.venv\Scripts\python.exe api_server.py
```

另开一个 PowerShell 窗口启动前端：

```powershell
.venv\Scripts\python.exe frontend\serve_frontend.py --port 5173
```

然后访问 `http://127.0.0.1:5173/?api=http://127.0.0.1:8610`。Windows 需要自行安装并加入 PATH 的 ffmpeg/ffprobe。

## 4. 当前链路与可选能力

### 默认可迁移链路

默认内部渲染使用项目自带的 FFmpeg 链路，素材来自：

1. `materials/` 本地素材目录；
2. 前端上传到项目存储的本地素材；
3. 在配置了 Pexels/Pixabay Key 且网络可用时，使用在线素材。

默认不会去读取另一台电脑上的 `MoneyPrinterTurbo` 目录，也不会隐式加载发布包外的代码。

### MoneyPrinter 搜索链路

原 MoneyPrinter 的在线搜索/下载能力不是核心包的硬依赖。需要在线素材时填写 `PEXELS_API_KEY` 或 `PIXABAY_API_KEY`；需要完整 MoneyPrinter 外部链路时，应把对应插件作为单独后端服务部署，再在 SaaS API 层接入，不能假设另一台电脑上存在原目录。

### 抖音 / 小红书抓取

`vendor/social_crawler/` 已随包提供代码，但真实登录抓取依赖：

- macOS 的 `osascript`；
- Chrome 已登录的抖音/小红书页面；
- 本机浏览器 Cookie / 登录态；
- 对应平台可访问的网络环境。

因此它不是无条件跨平台能力。需要在 macOS 上启用时运行：

```bash
./scripts/install-social-crawler.sh
```

真实 Cookie 文件不会打包。请根据 `vendor/social_crawler/cookie.example.txt` 在目标机器重新登录或重新配置，不要复制旧 Cookie。

## 5. 数据和账号迁移

- `.env` 需要在目标电脑重新创建，不能从发布包中恢复。
- 任务历史保存在目标机器的 `storage/`；发布包默认是空历史。
- 如果确实需要迁移历史，只迁移经过确认的 `storage/api_tasks`、`storage/tasks` 或指定业务目录，不要覆盖整个 `storage`，也不要迁移包含 OAuth Token 的文件。
- OceanEngine/巨量授权回调不能使用目标电脑无法访问的 `localhost`。本地测试可用 localhost；跨设备或服务器部署必须改成公网 HTTPS 回调，并在巨量引擎应用后台配置同一地址。
- 浏览器登录态不会随压缩包迁移，抖音/小红书需要在目标电脑重新绑定。

## 6. API 服务检查

启动后可以检查：

```bash
curl http://127.0.0.1:8610/api/health
```

前端通过 URL 参数指定 API：

```text
http://127.0.0.1:5173/?api=http://127.0.0.1:8610
```

若浏览器页面没有结果，先检查：

1. API 是否仍在运行；
2. `storage/logs/api-server.log`；
3. `.env` 的模型地址和 Key；
4. ffmpeg/ffprobe 是否可执行；
5. 浏览器访问的 `api` 参数是否和 API 端口一致。

## 7. 常用命令

```bash
# 安装 / 补依赖
./scripts/bootstrap.sh

# 验证安装
./scripts/verify-install.sh

# 启动 API + 前端
./scripts/start-stack.sh

# 只启动 SaaS API
./scripts/start-saas.sh

# 只启动前端
./scripts/start-frontend.sh

# 可选安装社交抓取依赖
./scripts/install-social-crawler.sh
```

## 8. 发布前安全检查

```bash
find . -name '.env' -o -name 'cookie.txt' -o -name '*.pem' -o -name '*.key'
```

发布包中不应出现真实 `.env`、Cookie、证书私钥或 API Key。若需要长期部署，建议把密钥放在目标机器的环境变量或密钥管理服务中，而不是提交到 Git 或写进压缩包。
