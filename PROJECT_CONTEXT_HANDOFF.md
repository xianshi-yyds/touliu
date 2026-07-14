# ExhibitFlow Lite 项目上下文交接文档

> 用途：将本项目当前的架构、功能、约束、运行方式和未完成事项交给新的对话继续开发。
> 更新时间：2026-07-14
> 项目目录：/Users/xianshi/Downloads/money/exhibitflow-lite

## 0. 新对话第一句话

将下面这段直接复制到新对话中：

~~~text
请先读取项目根目录的 PROJECT_CONTEXT_HANDOFF.md，再检查当前工作区、git diff 和运行状态；不要假设历史实现全部完成。先启动或检查服务，执行健康检查并确认当前端口，再根据文档继续。所有密钥只从 .env 读取，不要输出、复制或提交真实密钥。
~~~

新对话开始后，优先执行：

1. 读取本文件。
2. 检查 exhibitflow-lite 的 git status 和 git diff。
3. 检查本机 8501、8610、5173 端口以及正在运行的 Python/SSH 进程。
4. 启动服务并访问 /api/health。
5. 再根据用户当前明确的目标修改文件。

## 1. 产品目标

ExhibitFlow Lite 是一个面向展会运营的轻量 SaaS 工作台。产品主维度是“展会”，进入某个展会后分配和使用数字员工。

用户感知的核心是三个员工：

- 找爆款员工：检索抖音、小红书等平台的热点视频和样本，去重、排序、展示推荐样本。
- 生视频员工：根据主题、卖点、参考样本和素材来源生成文案、配音、字幕和最终视频。
- 投放员工：绑定巨量引擎 Marketing API，选择产出视频，创建官方项目和投放单元，并回流投放数据。

产品原则：

- 前端尽量轻量、清晰、SaaS 化，参考 Figma 的展会列表、数字员工和详情页层级。
- 账号绑定放在数字员工详情里，操作工作台不重复要求用户填写底层 Token 或广告主 ID。
- 长任务由后端异步执行，前端显示真实状态并轮询，不用假进度。
- 生成视频、投放草稿和数据复盘要分开，不能把未完成任务显示为绿色完成。
- 真实投放和模拟/安全草稿必须明确区分，不能把本地镜像数据当成已经真实投放。

## 2. 当前真实架构

### 2.1 当前项目是一个可迁移的独立项目

核心代码位于 exhibitflow-lite，不应默认依赖上级目录的 MoneyPrinterTurbo、new_video_download 或 social-auto-upload。

内部默认能力：

- Python HTTP API：api_server.py。
- SaaS 前端：frontend/index.html。
- 前端静态服务：frontend/serve_frontend.py。
- 本地任务和历史：storage/api_tasks、storage/creator_history、storage/oceanengine。
- 本地素材：materials。
- 内置 FFmpeg 生成链路：exhibitflow_lite/render.py。
- 文案、视觉分析、TTS：exhibitflow_lite/qwen.py、tts.py、pipeline.py。

可选能力：

- vendor/social_crawler：真实抖音/小红书抓取适配。
- vendor/money_pipeline：外部 MoneyPrinterTurbo 完整链路适配。
- vendor/social_auto_upload：社交平台发布适配。
- Pexels/Pixabay 在线素材：需要对应 API Key 和网络环境。

### 2.2 端口约定

- README 和默认配置通常使用 API 8610、前端 5173。
- 当前公开访问调试时，实际本机使用过 API 8501、前端 5173。
- API 端口由 EXHIBITFLOW_PORT 控制；前端端口由 FRONTEND_PORT 控制。
- 不要只看 README 判断端口，必须以当前进程、.env 和启动日志为准。

## 3. 重要文件和目录

~~~text
exhibitflow-lite/
├── api_server.py                 后端 API、任务队列、OAuth 回调、媒体服务
├── app.py                        旧版 Streamlit 入口，不是当前 SaaS 主入口
├── frontend/index.html           当前 SaaS 前端
├── frontend/serve_frontend.py    前端静态服务
├── exhibitflow_lite/
│   ├── config.py                 环境变量和路径配置
│   ├── pipeline.py               生成视频总链路
│   ├── qwen.py                   Qwen 视觉/文本相关调用
│   ├── tts.py                    TTS 适配
│   ├── stock.py                  Pexels/Pixabay/MoneyPrinter 在线素材搜索
│   ├── render.py                 内部 FFmpeg 合成
│   ├── social.py                  社交平台任务适配
│   └── publisher.py               发布能力适配
├── scripts/
│   ├── start-stack.sh            当前前后端一起启动
│   ├── start-saas.sh             只启动 API
│   ├── start.sh                  旧版 Streamlit 启动方式
│   ├── bootstrap.sh              初始化虚拟环境和依赖
│   ├── verify-install.sh          检查依赖和 FFmpeg
│   └── install-social-crawler.sh  安装可选抓取器
├── materials/                    本地素材库
├── storage/
│   ├── api_tasks/                 异步任务 JSON 和状态
│   ├── logs/                      API、前端和脚本日志
│   ├── downloads/                 抓取下载文件
│   ├── creator_history/           生视频历史版本
│   ├── pipeline_runs/             生成流水线运行记录
│   └── oceanengine/               OAuth、项目/单元本地镜像
├── vendor/                        可选外部能力，不是核心依赖
├── .env.example                   配置模板，不含真实值
├── README.md                      项目说明
├── DEPLOYMENT.md                  迁移和部署说明
└── pyproject.toml                 Python 依赖
~~~

## 4. 三个数字员工的目标交互

### 4.1 找爆款员工

数字员工详情应该显示：

- 抖音账号绑定状态和“绑定抖音”。
- 小红书账号绑定状态和“绑定小红书”。
- 账号绑定必须是真实登录/授权状态，不能只改前端文字。
- 账号状态写入持久化存储，重启后恢复；需要支持重新绑定和切换账号。

员工主页应该先显示两个大入口卡片，而不是每次进入都直接显示旧搜索表单：

1. 创建抓取任务：进入搜索配置和任务提交页。
2. 抓取历史记录：查看过去的检索任务、进度、样本结果和重新进入样本画廊。

执行要求：

- 后端创建真实异步任务。
- 前端轮询任务状态，显示排队、执行中、整理结果、已完成、失败。
- 任务执行阶段应有真实日志或阶段信息，不能只显示一个假百分比。
- 任务完成后进入推荐样本画廊。
- 推荐样本按互动分排序：点赞量 × 1 + 评论量 × 1.5 + 转发量 × 2。
- 画廊应显示平台、封面、标题、作者、点赞、评论、转发、互动分、排名。
- 选择参考样本后，明确跳转到生视频员工；不要在原页面隐式改变很多状态。
- 抖音和小红书没有可直接播放的公网视频时，只显示封面和“打开原视频/下载预览”等动作，不能渲染错误的 0 秒播放器。

### 4.2 生视频员工

数字员工详情应该显示：

- 素材库配置状态。
- MoneyPrinter 搜索链路状态。
- TTS 服务和音色配置状态。
- 当前账号/配置是否可用。

员工主页应该有两个大入口卡片：

1. 创作：进入当前生成链路。
2. 历史记录：查看已完成、生成中、失败的作品和每个版本的参数。

创作页建议分成可点击的阶段：

1. 确认最终口播。
2. 文案与声音。
3. 素材与字幕。
4. 成片与历史。

阶段状态必须遵循真实任务状态：只有后端完成该阶段才变绿；用户未点击下一步时，不能提前显示全部完成。

核心规则：

- 最终口播稿是配音、字幕和视频时长的唯一文本源。
- 默认在成片末尾追加 CTA：点击下方链接，立即报名吧。
- TTS 每个句子可以独立生成并按 MoneyPrinter 原始方式拼接；后续如果修改，必须验证音频时长、字幕时间轴和视频片段一致。
- 素材来源有两个明确选项：本地素材库、MoneyPrinter 在线搜索链路。
- 当前 API 的 MoneyPrinter/Pexels 逻辑偏全局：先根据整篇主题和口播生成搜索词，再建立覆盖整段视频时长的素材池，不是每一句单独搜索一个素材。
- 历史记录要保存文案、音色、素材来源、字幕样式、参考样本、生成结果和失败信息。
- 批量生产要作为独立后端任务，不能阻塞前端请求；前端只显示任务列表和每条进度。

### 4.3 投放员工

绑定必须发生在数字员工详情页：

- 账号类型：巨量引擎 Marketing API。
- 用户点击“立即登录/前往巨量”后走官方 OAuth。
- 回调后由后端换取 Token，并自动识别广告主、账号权限、可用投放资产。
- 详情页应显示未授权、授权中、已授权、授权失效、待补充资质等真实状态。
- 操作页不再要求用户手填 advertiser_id、access_token 等底层字段。
- OAuth 信息持久化，重启后恢复；授权切换要清理旧绑定或明确选择其他广告主。

投放员工工作台应该只有两个大的业务入口：

1. 投放面板：选择产出视频、投放目标、人群、预算、时间、落地页等，创建投放任务或安全草稿。
2. 数据复盘：按项目查看总体数据，并能下钻到每个视频单元，给出继续投放/停止投放建议。

“投放面板”流程：

1. 选择产出视频。视频来自生视频员工历史记录，名称不是“素材库”。
2. 选择投放目标、预算、开始/结束日期、人群和落地页等必要参数。
3. 确认后创建官方项目或项目下的投放单元/草稿。
4. 当前测试必须默认使用 DISABLE/安全草稿，不得在没有明确确认时产生真实消耗。

官方对象关系：

- 一个展会/一次营销活动对应一个官方项目。
- 一个项目包含多个投放单元。
- 一个投放单元对应一条视频及其配置。
- 项目页面查看总体数据，单元页面查看对应视频的数据。
- 本地 storage/oceanengine/projects 是本地镜像；只有官方接口成功返回的 project_id、promotion_id 等才可以标记为官方 ID。

“数据复盘”需要展示：

1. 消耗。
2. 展示。
3. 平均千次展现费用 = 消耗 / 展示 × 1000。
4. 点击数。
5. 点击率 = 点击数 / 展示 × 100%。
6. 平均转化成本 = 消耗 / 转化数；无转化时显示暂无数据或 0，不要制造有效结论。
7. 转化率 = 转化数 / 点击数 × 100%。
8. 转化数。
9. 有效播放量率 = 有效播放数 / 播放量 × 100%。

建议同时显示数据时间范围、数据来源、更新时间、样本量和“数据不足”的标识。建议不能只根据 0 数据下结论。

## 5. 当前 API 快速参考

基础：

- GET /api/health
- GET /api/config
- GET /api/materials
- POST /api/materials/upload?name=文件名
- GET /api/tasks
- GET /api/tasks/{task_id}
- GET /media/{storage-relative-path}

任务：

- POST /api/tasks/search
- POST /api/tasks/import-links
- POST /api/tasks/download
- POST /api/tasks/copy
- POST /api/tasks/tts
- POST /api/tasks/render
- POST /api/tasks/creator-pipeline
- POST /api/tasks/publish
- POST /api/tasks/delivery-draft
- POST /api/tasks/ocean-safe-launch

社交账号：

- GET /api/social/bindings
- POST /api/social/verify
- POST /api/social/clear

巨量引擎：

- GET /api/oceanengine/status
- GET /api/oceanengine/auth-url
- GET /api/oceanengine/callback
- POST /api/oceanengine/token
- POST /api/oceanengine/refresh-token
- GET /api/oceanengine/projects
- POST /api/oceanengine/advertisers
- POST /api/oceanengine/onboarding
- POST /api/oceanengine/report
- POST /api/oceanengine/report-config
- POST /api/oceanengine/video-upload
- POST /api/oceanengine/project-create
- POST /api/oceanengine/promotion-create

## 6. 配置和密钥

配置文件：exhibitflow-lite/.env。

只允许在 .env 中设置真实值；不要把真实值写进代码、README、交接文档、git 或聊天记录。

主要变量名：

- DEEPSEEK_API_KEY：文本 LLM。
- DEEPSEEK_BASE_URL：默认 https://api.deepseek.com/v1。
- DEEPSEEK_TEXT_MODEL：当前目标为 deepseek-chat。
- DASHSCOPE_API_KEY：Qwen 视觉/TTS 等能力。
- QWEN_TEXT_MODEL、QWEN_VL_MODEL、QWEN_TTS_MODEL：Qwen 模型名。
- PEXELS_API_KEY、PIXABAY_API_KEY：在线素材搜索。
- EXHIBITFLOW_RENDER_ENGINE：internal 或 moneyprinter。
- EXHIBITFLOW_MATERIAL_DIR：默认 ./materials。
- EXHIBITFLOW_COMPETITOR_DIR：默认 ./storage/downloads。
- SOCIAL_CRAWLER_DIR、MONEYPRINTERTURBO_DIR、SAU_BIN：可选外部插件路径。
- OCEANENGINE_APP_ID、OCEANENGINE_SECRET、OCEANENGINE_REDIRECT_URI：巨量 OAuth 应用配置。
- OCEANENGINE_ACCESS_TOKEN、OCEANENGINE_REFRESH_TOKEN、OCEANENGINE_ADVERTISER_ID：后端持久化或临时兼容配置。
- EXHIBITFLOW_PORT、FRONTEND_PORT：API 和前端端口。

不要在新对话中复述用户历史消息里出现过的真实 key。即使 key 曾经贴过，也只使用变量名并建议重新生成/轮换。

## 7. 本地启动和检查

首次安装：

~~~bash
cd /Users/xianshi/Downloads/money/exhibitflow-lite
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python .
cp .env.example .env
mkdir -p materials
./scripts/verify-install.sh
~~~

启动当前 SaaS 前后端：

~~~bash
cd /Users/xianshi/Downloads/money/exhibitflow-lite
./scripts/start-stack.sh
~~~

这会在前台启动 API 和前端，关闭终端会结束子进程。需要后台运行时使用 launchd、systemd、screen 或 tmux，并保留日志。

只启动 API：

~~~bash
cd /Users/xianshi/Downloads/money/exhibitflow-lite
./scripts/start-saas.sh
~~~

旧版 Streamlit：

~~~bash
cd /Users/xianshi/Downloads/money/exhibitflow-lite
./scripts/start.sh
~~~

验证：

~~~bash
curl -s http://127.0.0.1:8610/api/health
curl -I http://127.0.0.1:5173/
~~~

如果 .env 使用 EXHIBITFLOW_PORT=8501，将第一条改为：

~~~bash
curl -s http://127.0.0.1:8501/api/health
~~~

日志目录：storage/logs。重点查看 api-server.log、frontend-server.log 和对应任务日志。任务状态还可通过 GET /api/tasks 和 GET /api/tasks/{task_id} 查看。

## 8. 公开访问和 502 说明

当前公开地址曾经使用：

- 前端：https://xianshi.icu/exhibitflow/
- API：https://xianshi.icu/exhibitflow-api/api/health

这套公开访问不是后端直接部署在公网服务器，而是：

1. 本机运行 API 8501 和前端 5173。
2. 本机通过反向 SSH 隧道把本机端口映射到服务器回环地址。
3. 服务器 Nginx 将 /exhibitflow/ 和 /exhibitflow-api/ 反代到对应回环端口。

曾经出现 502 的直接原因是反向 SSH 隧道不存在或已断开；Nginx 本身正常，但连接不到上游。

当前映射关系：

- 远端 127.0.0.1:15173 -> 本机 127.0.0.1:5173。
- 远端 127.0.0.1:18610 -> 本机 127.0.0.1:8501。

恢复前应先确认本机两个服务存在，再重建隧道。命令中的服务器地址、账号和密钥不要写入代码或文档，使用运维环境变量或 SSH 配置。

~~~bash
ssh -f -N -T -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -R 127.0.0.1:18610:127.0.0.1:8501 -R 127.0.0.1:15173:127.0.0.1:5173 -p 2222 YOUR_SSH_USER@YOUR_SERVER
~~~

本机演示环境也提供了带自动重连的临时隧道脚本：

~~~bash
./scripts/keep-reverse-tunnel.sh
~~~

它会在 SSH/VPN 断开后自动重连；长期运行建议改为 launchd/systemd 管理。

检查顺序：

1. 本机 curl API health。
2. 本机 curl 前端首页。
3. 查看 SSH 进程和端口转发。
4. 在服务器上 curl 127.0.0.1:15173 和 127.0.0.1:18610。
5. 最后访问公网域名。

长期稳定方案是把 API 和任务 worker 部署到服务器，并用 systemd/Docker 管理；反向隧道只适合测试或临时演示。本机睡眠、VPN 变化、网络波动、终端关闭都会再次导致 502。

## 9. 当前已知状态

- 公开前端和 API 最近一次检查已经恢复：公网前端返回 HTTP 200，公网 health 返回 ok=true。
- 本地当前曾使用 API 8501、前端 5173；项目默认文档仍保留 API 8610 的可迁移默认值。
- 文本 LLM 目标配置是 DeepSeek；真实可用性以 /api/health 和任务日志为准。
- Qwen 视觉/TTS 依赖 DashScope 网络和额度；健康检查可能显示 ReadTimeout/unreachable，不代表代码一定错误。
- publisher_configured=false 时，不要声称已经完成真实社交发布。
- crawler_configured=true 只代表适配器或脚本存在，不等于浏览器登录、Cookie、网络和抓取链路都已验证。
- OceanEngine 是否可真实投放，要看 OAuth、广告主资质、应用权限、落地页/转化资产和官方接口响应；本地项目镜像不能证明已投放。
- 老历史记录可能包含 /tmp/final.mp4 等过期路径，前端应该优先使用 /media/storage/...；遇到 404 先检查任务结果中的实际文件路径。

## 10. 未完成和需要优先验证的问题

### P0：不能假成功

- 所有任务阶段状态必须来自后端真实状态。
- 搜索、文案、TTS、渲染失败必须展示失败原因和日志入口。
- 任务重启后要能恢复可恢复任务；投放创建类任务不能自动重试造成重复项目或重复单元。
- 生成完成后必须真的能在历史记录和媒体 URL 中播放。

### P0：三个员工入口和层级

- 找爆款员工：创建任务、抓取历史两个大入口；执行中显示进度；结果显示推荐排名。
- 生视频员工：创作、历史记录两个大入口；创作和历史不能用小按钮挤在页面顶部。
- 投放员工：投放面板、数据复盘两个大入口；进入后再显示对应工作台。
- 数字员工详情负责全局绑定；操作页面不再出现重复绑定字段。

### P1：真实账号状态

- 抖音、小红书绑定需要真实登录/授权状态和持久化状态。
- 巨量 OAuth 回调后需要刷新详情页和员工卡片，显示已授权和广告主信息。
- 重启服务后读取 OAuth 持久化信息，并提供切换账号流程。
- 账号状态不能靠前端 query 参数或静态文案伪造。

### P1：素材和生成链路

- 明确区分本地素材库和 MoneyPrinter 在线搜索。
- 验证全局素材搜索、音频拼接、字幕时间轴和视频拼接是否一致。
- 验证批量任务不会阻塞前端，也不会串任务状态。
- 验证 CTA 只追加一次，并进入最终配音和字幕。

### P1：投放项目/单元

- 官方项目创建只创建一次。
- 多条视频对应同一项目下的多个单元。
- 项目详情能列出所有单元。
- 单元能绑定视频、promotion_id 和数据时间范围。
- 数据复盘需要区分项目汇总和单元明细，并显示数据更新时间。

## 11. 迁移和安全注意事项

可以迁移到另一台电脑，但不要复制以下内容：

- .env 中的真实密钥。
- storage/oceanengine/oauth.json。
- 浏览器 Cookie、社交平台登录态、个人账号缓存。
- 生成的临时缓存和大体积媒体，除非明确需要。
- .venv 和系统相关二进制。

迁移时应复制：

- 源代码、frontend、scripts、pyproject.toml。
- .env.example、README.md、DEPLOYMENT.md。
- materials 中需要保留的本地素材。
- 需要保留的历史 JSON，但先确认其中没有 Token、Cookie 或个人隐私。

另一台电脑重新执行 bootstrap，安装 Python、FFmpeg 和依赖，再单独创建 .env。OAuth 回调地址必须是新环境可访问的 HTTPS 地址，并与巨量应用后台配置完全一致。

## 12. 开发纪律

- 修改前先读现有实现和任务状态模型，不要在前端造一个新的假状态体系。
- 不要把外部 MoneyPrinterTurbo 目录写死到核心代码。
- 不要把密钥硬编码进 Python、HTML、日志、截图或 Markdown。
- 不要用旧历史记录冒充刚刚生成的结果。
- 不要把“模拟投放”“安全草稿”“官方真实投放”混为一谈。
- 不要为了修 UI 删除后端可追溯日志。
- Streamlit 不允许超过一层 columns 嵌套；优先避免复杂嵌套布局，当前 SaaS 前端应优先使用独立 HTML/API。
- 每次改动后至少检查：语法、健康接口、任务创建、任务轮询、媒体 URL、浏览器页面。

## 13. 新对话推荐执行顺序

1. 读取本文件和 README/DEPLOYMENT。
2. 执行 git status、git diff --stat，确认当前未提交改动。
3. 读取 .env 的变量名即可，不输出值。
4. 启动 API 和前端，验证 health。
5. 先跑一个本地素材的最小生视频任务，确认 copy -> TTS -> render -> history 全链路。
6. 再验证找爆款任务的真实异步状态和样本画廊。
7. 再验证数字员工详情里的账号绑定状态持久化。
8. 最后验证投放安全草稿的项目/单元结构和数据复盘页面。
9. 每一阶段完成后再做 UI 收敛，不要同时大改三个员工。

## 14. 当前 Git 工作区提示

交接前项目存在未提交修改，涉及配置、API、前端和启动脚本；还存在若干 .DS_Store 噪声文件。新对话不要直接 reset、clean 或删除未提交内容。先查看 diff，确认哪些是本轮已完成改动，哪些是历史遗留。

本文件本身是交接文档，不代表所有待办都已经实现。任何“已完成”都必须以代码、日志和浏览器验收为准。

## 15. 交接结论

当前 ExhibitFlow Lite 已经具备独立项目骨架、SaaS 前后端、异步任务、视频生成、素材来源切换、OAuth/投放接口骨架和项目/单元本地镜像；公开访问最近已通过反向 SSH 隧道恢复。

但它仍然是开发/测试态：真实社交账号抓取、真实巨量投放、在线素材、Qwen 网络、OAuth 资质和长任务稳定性必须逐项实测。新对话应先以“可验证的真实链路”为准，不要仅依据页面上的状态文字判断功能完成。
