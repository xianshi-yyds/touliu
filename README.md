# ExhibitFlow Lite

多平台投放扩展说明见 [`docs/platform-integrations.md`](docs/platform-integrations.md)。当前已保留抖音巨量链路，新增小红书 Rnote 公开检索和视频号腾讯广告创意只读适配器。

真正可迁移的轻量版展会短视频工作台。默认不依赖父目录里的
`MoneyPrinterTurbo`、`new_video_download` 或 `social-auto-upload`。

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
- **生视频员工**：最终口播稿、配音和字幕共用一份文案；默认结尾行动引导为“点击下方链接，立即报名吧”，支持修改。Qwen TTS 与免密 Edge TTS 可切换。
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
