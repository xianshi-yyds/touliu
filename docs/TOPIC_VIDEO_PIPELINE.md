# 主题驱动动态成片链路

当前项目的“生视频员工”已经可以把主题输入替换成动态展会成片模板。页面仍然使用原有的异步任务、进度、历史记录和投放交接机制。新的动态成片任务默认使用 `agent_mode: "internal-skill"`：内部 Video Agent 读取项目内的展会视频 Skill，生成结构化 VideoPlan，再交给 Remotion 执行。`creator-pipeline` 在收到 `render_engine: "remotion-topic"` 时，会执行下面这条链路：

```text
展会信息表单 / 口播稿 / 宣传重点 / 用户受众
              ↓
背景信息进入内部 Video Agent Skill
              ↓
内部 Skill 生成关联双文案：完整旁白 + 短屏幕文案
              ↓
内部 Skill 生成受约束的视觉检索词
              ↓
用户选择 / 参考视频画像 / Agent Skill 建议共同形成生产模式
              ↓
后端校验为 montage / avatar / hybrid（不可用时降级 montage）
              ↓
Pexels / Pixabay 横屏网络素材检索与下载
              ↓
Edge TTS 逐句配音（失败时保留已有回退机制）
              ↓
可选 RunningHub 数字人口播素材（只调用一次）
              ↓
AutoPartsKinetic Remotion 动效模板
              ↓
final.mp4 + manifest.json + generation-summary.md
```

## 页面使用

进入“生视频员工”后，在“展会信息”区域填写：

- 本次视频主题 / 活动名称；
- 想要宣传的点，例如源头工厂、产品比较、海外采购对接价值；
- 用户受众，例如海外采购、汽配经销商、渠道商和专业观众；
- 展会补充信息，例如日期、地点、核心痛点和展会事实；
- 可选数字亮点，例如 `2000+源头工厂, 8大品类, 60000㎡`；
- 可选英文网络检索词。

在“视频参数”中选择目标时长、视频比例、Edge TTS 音色、成片方式、字体样式和镜头转场；BGM 默认关闭，也可以上传 MP3/WAV/M4A 等音频后选择并设置音量。成片方式包括智能判断、素材混剪、数字人口播和混合成片。点击“生成口播稿”后可以编辑旁白，确认后点击“开始生成视频”。宣传重点和用户受众会作为背景信息同时交给口播文案、屏幕短文案和网络素材检索；网络素材仍由服务端自动检索，页面不再展示素材配置卡片。

三种实际执行模式：

- `montage`：网络/用户素材作为所有镜头背景；
- `avatar`：RunningHub 人物视频作为主画面，动态文字继续由 Remotion 编排；
- `hybrid`：素材混剪为主，开场、中段和结尾按场景显示右上角圆形数字人。

`auto` 不是第四种渲染器，而是决策入口：优先采用参考视频 VL 画像，其次采用内部 Agent Skill 建议，没有可靠画像时稳定使用 `montage`。数字人服务失败不会让整条任务失败，系统会保留配音和网络素材并自动改为 `montage`。

主题信息推荐使用这种格式。字段可以少填，未填写的内容不会被硬编进视频：

```md
# 2026 国际智能制造展

想要宣传的点：源头工厂、方案比较和现场沟通价值
用户受众：制造企业负责人、采购和渠道商
目标受众痛点：信息分散，方案难比较，沟通成本高
展会解决路径：集中查看产品，现场比较方案，直接沟通适配性
日期：2026年9月18日—20日
地点：上海新国际博览中心

亮点：
- 2000+源头工厂
- 8大品类
- 60000㎡展示空间

CTA：现在了解展会信息，安排你的参观计划
```

如果已有确定口播稿，可以在“已有文案”中直接粘贴；未提供口播稿时，服务端会优先调用 Qwen，模型不可用时使用当前填写的展会信息构造保守版口播，不会因此伪造规模或订单数据。

## API 调用

服务启动后，可以直接提交现有任务接口：

```bash
curl -X POST http://127.0.0.1:8610/api/tasks/creator-pipeline \
  -H 'Content-Type: application/json' \
  -d '{
    "video_project_id": "topic-smart-factory-demo",
    "render_engine": "remotion-topic",
    "video_template": "AutoPartsKinetic",
    "agent_mode": "internal-skill",
    "topic": "2026 国际智能制造展",
    "event_name": "2026 国际智能制造展",
    "theme_text": "想要宣传的点：源头工厂、方案比较和现场沟通价值\n用户受众：制造企业负责人、采购和渠道商\n亮点：2000+源头工厂, 8大品类\n地点：上海新国际博览中心",
    "promo_focus": "源头工厂、方案比较和现场沟通价值",
    "audience": "制造企业负责人、采购和渠道商",
    "background_context": "想要宣传的点：源头工厂、方案比较和现场沟通价值\n用户受众：制造企业负责人、采购和渠道商",
    "script": "如果你正在关注智能制造，现场可以集中了解产品与方案。\n8大品类，帮助你快速比较不同方向。",
    "highlights": ["2000+源头工厂", "8大品类"],
    "material_source": "pexels",
    "tts_service": "edge",
    "voice": "zh-CN-XiaoxiaoNeural",
    "aspect_ratio": "16:9",
    "production_mode": "auto",
    "reference_profile": {"content_type": "hybrid", "confidence": 0.88},
    "cta_text": "现在了解展会信息，安排你的参观计划"
  }'
```

响应中的 `id` 是异步任务编号。轮询 `/api/tasks/{id}`，成功后从 `result.summary.preview_url` 预览，从 `result.summary.final_video` 获取项目内绝对路径；`result.summary.summary_markdown_url` 可以查看本次稳定生成记录。`result.summary.voiceover_script` 是旁白稿，`result.summary.screen_copy` 是屏幕短文案，两者相关但不逐句重复。

## 成片输出

每次任务会保存到：

```text
storage/video_projects/<video_project_id>/
├── final.mp4
├── manifest.json
├── remotion-props.json
├── theme-input.md
├── script.txt
└── generation-summary.md
```

`manifest.json` 会额外记录 `voiceoverScript`、`screenCopy`、`backgroundContext`、`copyRelation`、`productionPlan` 和 `digitalHuman`。这份 manifest 是重渲染和审计输入，`generation-summary.md` 是面向查看的稳定 Markdown 记录。

网络下载素材保存在 `storage/online_materials/topic-video-<id>/`，同时按项目编号复制到 `remotion_exhibition_promo/public/assets/topic-video/<id>/`，因此历史成片的 manifest 可以被重新渲染。

## 部署要求

除原有 Python、FFmpeg、Pexels/Pixabay 和 Edge TTS 配置外，动态链路需要：

- Node.js 18+；
- `remotion_exhibition_promo/node_modules`，首次部署执行 `cd remotion_exhibition_promo && npm ci`；
- `.env` 中至少配置 `PEXELS_API_KEY` 或 `PIXABAY_API_KEY`；
- 若不提供手动口播稿，建议同时配置 `TEXT_LLM_API_KEY` / `TEXT_LLM_BASE_URL`。
- 数字人口播或混合成片需要 RunningHub 配置和一张人物正面图；纯混剪不依赖 RunningHub。

`./scripts/bootstrap.sh` 现在会在检测到 Node/npm 后自动安装 Remotion 依赖；`./scripts/verify-install.sh` 会检查 Remotion CLI 是否存在。

当前模板会按 manifest 选择字体栈（冲击粗体、科技窄体、杂志衬线、等宽数据），并在相邻镜头边界使用直接切换、淡入淡出、横向擦除或闪白转场。文字仍包含逐字裁切擦除、从下方弹出、弹性放大、轻微旋转、扫光、高亮闪白、数字 0→目标值（0.5 秒）以及结尾 CTA 呼吸放大；BGM 作为独立低音量循环音轨叠加，默认关闭，也不使用卡片式信息面板。
