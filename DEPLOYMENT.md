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
# Qwen 文本模型（OpenAI-compatible）
TEXT_LLM_API_KEY=你的百炼_KEY
TEXT_LLM_BASE_URL=https://ws-7s8hh8dksjtq6j2u.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
TEXT_LLM_MODEL=qwen3.6-plus
TEXT_LLM_ENABLE_THINKING=false

# Qwen 视觉 / Qwen TTS（按需）
DASHSCOPE_API_KEY=你的百炼_KEY
QWEN_VL_MODEL=qwen3-vl-plus
QWEN_TTS_MODEL=qwen3-tts-flash
QWEN_TTS_VOICE=Serena
```

没有填写在线模型 Key 时，仍可以启动页面并使用本地素材链路；文案、视觉理解或 Qwen TTS 请求会提示配置缺失。

### PyCaps 字幕渲染（可选增强）

选择前端的“PyCaps 动态高亮”模板时，服务会用 Chromium 将字符级字幕和动画渲染成最终成片；普通模板仍保留原有 Pillow/FFmpeg 回退链路。安装方式：

```bash
./scripts/install-pycaps.sh
```

PyCaps 需要额外的 Chromium 和 Linux 图形库。Amazon Linux 2023 可补充：

```bash
sudo dnf install -y libxcb libX11 libXcomposite libXdamage libXext libXfixes libXrandr libgbm libdrm pango cairo atk at-spi2-atk cups-libs gtk3 nss alsa-lib
```

历史成片会保留无字幕基底、配音和字幕时间轴。只要没有“保存到视频创作包”，就可以从历史记录重新打开字幕预览并更换字体/动画；保存后该版本会锁定。

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

### TikHub 服务端抖音 / 小红书检索

新服务器不适合运行依赖 macOS `osascript` 的本地浏览器抓取器时，可以改用 TikHub 的抖音和小红书公共检索 API。它不依赖用户浏览器登录，任务由 ExhibitFlow API 在服务器端执行：

```env
TIKHUB_API_KEY=你的 TikHub API Key
TIKHUB_BASE_URL=https://api.tikhub.dev
SOCIAL_SEARCH_PROVIDER=auto
```

`auto` 模式会优先使用 TikHub 检索抖音；如果配置 `RNOTE_API_KEY`，小红书优先使用 Rnote，否则复用 TikHub，两个 Key 都未配置时才回退本地适配器。托管检索接口按请求计费，余额不足时任务会明确失败，不会伪造空结果。视频号投放与腾讯广告创意只读链路见 [`docs/platform-integrations.md`](docs/platform-integrations.md)。

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

## 9. EC2 长期部署

生产部署建议把 API 和前端放在同一台服务器上，不再依赖本机反向 SSH 隧道：

- API 监听 `127.0.0.1:8501`，由 `systemd` 守护；
- 前端监听 `127.0.0.1:5173`，由 `systemd` 守护；
- Nginx 暴露 `/exhibitflow/` 和 `/exhibitflow-api/`；
- FFmpeg 和中文字体必须安装在服务器上；
- 域名 A 记录指向服务器的固定公网 IP 后，再用 Certbot 签发 HTTPS。

仓库中的 `deploy/` 目录提供了 systemd/Nginx 模板。实际部署时将服务用户、项目路径和域名替换为目标环境值，并确保云安全组放行 TCP 80/443。

巨量引擎回调继续使用：

```text
https://xianshi.icu/exhibitflow-api/api/oceanengine/callback
```

### 与旧服务器隔离的子域名部署

如果根域名还承载旧服务器上的生图或其他项目，不要让根域名同时配置两个不同服务器的 A 记录。推荐保留：

```text
xianshi.icu           -> 旧服务器
www.xianshi.icu       -> 旧服务器
employee.xianshi.icu  -> 数字员工新服务器
```

新服务器使用 `deploy/nginx-exhibitflow-subdomain.conf`：根路径反代前端
5173，`/api/` 反代 API 8501。生产环境回调地址改为：

```text
https://employee.xianshi.icu/api/oceanengine/callback
```

并在巨量开放平台应用后台登记完全相同的地址。这样旧服务器的根域名和
其他项目不受影响，数字员工的代码、任务、素材、渲染和模型服务全部在
新服务器内运行。

抖音/小红书抓取脚本目前的浏览器绑定实现依赖 macOS `osascript` 和 Chrome。API、渲染、TTS、投放回调可以直接在 Linux/EC2 运行；如果要把“登录绑定后再抓取”也完全放到 Linux，需要另外接入 Playwright/远程浏览器会话，不能只复制 macOS 的浏览器登录流程。
