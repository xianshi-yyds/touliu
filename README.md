# ExhibitFlow Lite

主题驱动的横屏动态展会成片链路见 [`docs/TOPIC_VIDEO_PIPELINE.md`](docs/TOPIC_VIDEO_PIPELINE.md)：用户输入主题或上传 Markdown → 网络检索素材 → Edge TTS → Remotion 动态文字模板 → 视频与 `generation-summary.md`。

多平台投放扩展说明见 [`docs/platform-integrations.md`](docs/platform-integrations.md)。当前已保留抖音巨量链路，新增小红书 Rnote 公开检索和视频号腾讯广告创意只读适配器。

真正可迁移的轻量版展会短视频工作台。默认不依赖父目录里的
`MoneyPrinterTurbo`、`new_video_download` 或 `social-auto-upload`。

## 服务器访问与项目位置

当前数字员工服务部署在独立服务器上，和旧服务器上的生图及其他项目隔离。

| 项目 | 当前值 |
| --- | --- |
| 服务器公网 IP | `3.136.217.101` |
| SSH 用户 | `ec2-user` |
| 本机 SSH 私钥 | `~/Downloads/xianshi.pem` |
| 服务器项目目录 | `/opt/exhibitflow-lite` |
| 线上地址 | `https://employee.xianshi.icu/` |
| API 内部端口 | `127.0.0.1:8501` |
| 前端内部端口 | `127.0.0.1:5173` |

### SSH 登录

在本机终端执行：

```bash
chmod 400 ~/Downloads/xianshi.pem

ssh -o IdentitiesOnly=yes \
  -i ~/Downloads/xianshi.pem \
  ec2-user@3.136.217.101
```

登录后进入项目：

```bash
cd /opt/exhibitflow-lite
```

私钥只保存在本机，不要上传到服务器、提交到 Git 或写入 README。

### 服务状态与健康检查

服务器使用 `systemd` 守护前后端服务，Nginx 将域名请求转发到这两个本机端口：

```bash
# 查看服务状态
sudo systemctl status exhibitflow-api --no-pager
sudo systemctl status exhibitflow-frontend --no-pager

# API 健康检查
curl -fsS http://127.0.0.1:8501/api/health

# 检查公网入口
curl -I https://employee.xianshi.icu/
```

常用运维命令：

```bash
# 重启服务
sudo systemctl restart exhibitflow-api
sudo systemctl restart exhibitflow-frontend

# 查看实时日志
sudo journalctl -u exhibitflow-api -f
sudo journalctl -u exhibitflow-frontend -f

# 查看项目日志
tail -f /opt/exhibitflow-lite/storage/logs/api-server.log
tail -f /opt/exhibitflow-lite/storage/logs/frontend-server.log
```

服务器项目的主要目录：

```text
/opt/exhibitflow-lite/
├── api_server.py                  后端 API、异步任务和回调
├── frontend/index.html            SaaS 前端页面
├── frontend/serve_frontend.py     前端静态服务
├── exhibitflow_lite/              文案、TTS、素材、渲染和平台适配
├── materials/                     展会和品牌素材
├── storage/                       任务、历史、媒体、日志和平台状态
├── vendor/                        可选的抓取/外部链路
├── .env                           服务器私有配置，不提交
└── .venv/                         Python 虚拟环境
```

本地修改同步到服务器前，先备份服务器对应文件；不要覆盖服务器上的 `.env`、授权状态和 `storage/`。同步后通常执行：

```bash
sudo systemctl restart exhibitflow-api
sudo systemctl restart exhibitflow-frontend
curl -fsS http://127.0.0.1:8501/api/health
```

如页面仍显示旧版本，使用浏览器强制刷新：macOS 按 `Command + Shift + R`。

## 当前项目功能

ExhibitFlow Lite 是按“展会”组织业务的数字员工 SaaS 工作台。进入某个展会后，用户可以在同一个展会上下文中管理素材、生产视频并准备投放。

### 1. 展会与素材库

- 展会列表：新建或进入已有展会。
- 展会级隔离：不同展会的素材、任务、历史版本和产出互不混用。
- 素材库：上传展会图片、视频和品牌素材，作为后续生视频的本地素材来源。
- 客户画像：根据当前展会内置加载，仅作为生成口播稿的上下文，不要求用户重复填写独立任务。

### 2. 找爆款员工

- 通过 TikHub 检索抖音公开内容，也支持小红书 Rnote 公开检索或手动导入链接。
- 创建异步检索任务，后台执行搜索、去重、互动分计算和样本整理。
- 按点赞、评论、转发等互动数据生成推荐样本包。
- 支持查看检索历史、样本详情，并将参考样本用于后续视频创作。

### 3. 生视频员工

- 根据当前展会信息、客户画像、参考样本和素材生成口播稿。
- 选择音色并生成配音，口播稿同时作为字幕和时长计算的文本源。
- 支持本地素材库或网络素材检索两种素材来源。
- 使用 FFmpeg 生成竖屏推广视频，并保存无字幕基底、字幕时间轴和成片历史。
- 在成片前实时预览字幕效果，可切换视觉预设、字体、入场动画、字幕大小和位置。
- 未保存到视频创作包的历史版本仍可重新编辑字幕；保存后版本锁定。

### 4. 投放员工

- 通过巨量引擎 OAuth 绑定广告账号。
- 从生视频员工的历史产出中选择视频，配置投放目标、预算、时间、人群和落地页。
- 默认创建安全草稿，不会未经确认直接产生真实消耗。
- 预留小红书、视频号/腾讯广告等平台扩展接口；当前腾讯广告适配以只读创意接口为主。

### 5. 后台任务与数据持久化

- API 负责检索、AI、TTS、视频渲染、字幕后处理和投放任务。
- 前端通过任务接口轮询真实状态，不依赖页面停留；离开页面后任务仍会在服务器继续执行。
- 任务状态、日志、媒体文件、历史版本和平台绑定状态保存在服务器 `storage/` 中。
- 页面刷新或重新进入员工后，可以继续查看进行中、已完成和失败的任务。

## 内置能力

- 手动导入抖音/小红书链接作为参考样本
- 上传本地参考视频/图片
- Qwen 文案生成、Qwen 视频理解
- 内部轻量成片引擎：素材目录 + 文案 -> 竖屏视频
- 本地 storage 管理：候选清单、上传样本、TTS、渲染结果
- SaaS 版前端 + 后台任务 API：前端只负责交互，检索/AI/成片/发布/投放都通过接口异步执行

## 可选插件

这些插件不再是默认依赖。复制 `exhibitflow-lite` 到另一台电脑后，即使没有它们，应用也能打开并生成基础视频。

- `vendor/social_crawler`：真实抖音/小红书检索和下载
- `vendor/money_pipeline`：调用完整 MoneyPrinterTurbo 成片链路
- `vendor/social_auto_upload`：真实发布到抖音/小红书

如需启用，在 `.env` 里配置：

```env
SOCIAL_CRAWLER_DIR=./vendor/social_crawler
MONEYPRINTERTURBO_DIR=./vendor/money_pipeline
SAU_BIN=./vendor/social_auto_upload/.venv/bin/sau
EXHIBITFLOW_RENDER_ENGINE=moneyprinter
```

如果服务器不适合运行依赖 macOS Chrome 登录态的本地抓取器，可以配置 TikHub 的服务端抖音公共检索：

```env
TIKHUB_API_KEY=your_tikhub_api_key
TIKHUB_BASE_URL=https://api.tikhub.dev
SOCIAL_SEARCH_PROVIDER=auto
```

`auto` 模式下，抖音优先使用 TikHub，未配置 Key 时回退本地抓取器；小红书仍使用现有本地适配器。TikHub 专用搜索接口按请求计费，分页越多费用越高。

默认保持：

```env
EXHIBITFLOW_RENDER_ENGINE=internal
EXHIBITFLOW_MATERIAL_DIR=./materials
EXHIBITFLOW_COMPETITOR_DIR=./storage/downloads
```

MoneyPrinter 在线素材链路使用 Pexels，需要额外配置：

```env
PEXELS_API_KEY=your_pexels_api_key
```

文本口播稿和在线搜索词使用阿里云百炼 Qwen 的 OpenAI 兼容接口；视频理解和
Qwen TTS 使用 DashScope。真实 API Key 只放在本机 `.env`，不要提交到仓库：

```env
TEXT_LLM_API_KEY=your_dashscope_api_key
TEXT_LLM_BASE_URL=https://ws-7s8hh8dksjtq6j2u.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
TEXT_LLM_MODEL=qwen3.6-plus
DASHSCOPE_API_KEY=your_dashscope_api_key
QWEN_TTS_MODEL=qwen3-tts-flash
QWEN_TTS_VOICE=Serena
```

生视频员工支持两种素材来源：

- **本地素材库**：使用 `materials/` 中上传的品牌和展会素材。
- **MoneyPrinter 在线搜索**：根据整篇口播生成英文检索词，从 Pexels 搜索、去重并下载足够时长的素材。

每次完整生成都会保留为历史版本，可恢复当时的文案、音色、字幕模板和素材来源后再次生成。

## 迁移方式

复制整个文件夹即可：

```text
exhibitflow-lite/
```

到新电脑后：

```bash
cd exhibitflow-lite
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python .
cp .env.example .env
mkdir -p materials
./scripts/start.sh
```

## SaaS 版交互入口

新的交互形态不再把复杂控件堆在 Streamlit 页面里，而是让用户只感知三个员工：

- 找爆款员工：检索抖音/小红书参考样本，按互动表现排序。
- 生视频员工：根据参考样本和业务卖点生成口播稿、语音和成片。
- 投放员工：检查巨量账号配置，生成投放草稿，并承接后续报表展示。

前端使用：

```text
frontend/index.html      # SaaS 风格前端
api_server.py            # 后台任务 API
storage/api_tasks/*.json # 任务状态和结果
```

启动后端 API：

```bash
cd exhibitflow-lite
./scripts/start-saas.sh
```

如果希望一次启动前后端开发栈：

```bash
./scripts/start-stack.sh
```

后端默认地址：

```text
http://127.0.0.1:8610
```

开发时也可以把前端作为独立静态站点启动。前端不执行检索、模型调用或成片逻辑，
只通过 API 创建任务、轮询状态和展示结果：

```bash
./scripts/start-frontend.sh
```

前端单独部署时打开：

```text
http://127.0.0.1:5173/?api=http://127.0.0.1:8610
```

生产部署时可把 `frontend/` 放到 Nginx、对象存储或任意静态托管，
通过 `?api=https://你的后端域名` 或 `window.EXHIBITFLOW_API_BASE` 指向后端。

### 三个员工的统一交互

- **找爆款员工**：提交后停留在当前页面，“当前任务”显示后台进度，结果自动进入样本画廊。
- **生视频员工**：输入宣传重点和用户受众后，分别生成关联的完整旁白与屏幕短文案；默认结尾行动引导为“点击下方链接，立即报名吧”，支持修改。动态主题模板使用 Edge TTS。
- **投放员工**：OAuth 绑定后直接选择成片、投放目标和预算。真实链路测试只创建 `DISABLE` 状态的项目和推广，必须勾选安全确认，不会自动开启消耗。

技术日志默认收在“后台任务 → 查看技术详情”，员工主页只显示业务状态、进度和成果。

### 字幕模板与最后一步预览

成片生成时会同时保存一份“带配音、无字幕”的预览基底、字幕时间轴和默认成片。成片页先用 CSS/DOM 叠加层实时预览，点击不同字体样式或动画不会重新搜索素材、生成配音或重做视频；确认后才提交 `caption-style` 后处理任务，把最终选择硬烧进可投放成片。

当前支持四套 FFmpeg/Pillow 视觉模板，以及一套真实 PyCaps 字幕模板：

- `viral`：爆款强调，圆润粗体、黑色厚描边、橙黄重点词，接近短视频参考样式。
- `business`：专业展会，白字深色描边、蓝色业务重点词。
- `minimal`：极简商务，半透明底板、绿色重点词。
- `energetic`：活力招展，橙色大字、红色描边和强调下划线。
- `pycaps_hype`：PyCaps 动态高亮，字符按时间轴逐字进入，当前字放大并高亮。

字幕动画可独立选择：弹入、上滑、淡入、打字机。可手动填写重点词；留空时自动识别“专业买家、人流量、展位、招商、报名”等转化词。Edge TTS 会记录真实词级时间戳，字幕按语音时间切换；其他音频采用句子级时间轴兜底。

浏览器预览层和最终成片都使用独立的字幕基底；安装 `caption-preview` 依赖后，选择 `pycaps_hype` 会调用真实 PyCaps/Chromium 渲染，未安装时其他四套模板仍走原有回退链路。历史记录中只要没有保存到视频创作包，就会显示“预览/编辑字幕”；保存视频创作包后，该版本的字幕编辑状态会持久化锁定。

接口形态：

```text
GET  /api/health
GET  /api/config
GET  /api/materials
POST /api/materials/upload?name={filename}
GET  /api/tasks
GET  /api/tasks/{task_id}
POST /api/tasks/search
POST /api/tasks/import-links
POST /api/tasks/download
POST /api/tasks/copy
POST /api/tasks/tts
POST /api/tasks/render
POST /api/tasks/caption-style
POST /api/tasks/publish
POST /api/tasks/delivery-draft
POST /api/tasks/ocean-safe-launch
GET  /media/{storage-relative-path}
```

后台会校验必填项、隐藏任务文件中的 Token，并在服务重启时把未完成任务标记为中断。
成片和语音任务会返回可在页面直接预览的媒体地址。真实接巨量创建项目/推广时，
继续扩展 API 即可，不需要再把官方枚举和 JSON 堆回前端。

如果不用 `uv`，也可以使用任意 Python 3.10+：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
./scripts/start.sh
```

把可用素材放进：

```text
materials/
```

支持 `mp4/mov/webm/mkv/jpg/png/webp`。

## 依赖

内部成片引擎需要本机有 `ffmpeg` 和 `ffprobe`。

```bash
ffmpeg -version
ffprobe -version
```

如果命令不存在，先安装 FFmpeg。

## 说明

- 内部成片引擎是轻量实现，不等同于完整 MoneyPrinterTurbo。
- 真实平台检索和发布因为涉及登录态、浏览器、平台限制，仍建议作为可选插件放在 `vendor/` 下。
- Qwen TTS 取决于百炼模型权限；Key 不可用时可切换到免密 Edge TTS。
