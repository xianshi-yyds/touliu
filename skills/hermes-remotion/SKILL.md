---
name: remotion-exhibition
version: 0.1.0
description: 通过现有 ExhibitFlow API，把展会信息规划成双文案和素材检索方案，再交给 AutoPartsKinetic Remotion 稳定渲染。
metadata:
  hermes:
    tags: [video, remotion, exhibition, kinetic-typography]
    category: creative
---

# Hermes + Remotion 展会视频 Skill

## 什么时候使用

当用户要求根据展会主题、宣传重点、用户受众和事实信息生成宣传视频时使用。Hermes 负责理解需求、生成结构化视频方案和两套关联文案；服务器上已经验证过的 `AutoPartsKinetic` Remotion 负责素材编排、Edge TTS、动态文字和最终 MP4 渲染。

## 重要边界

- 只调用本机 ExhibitFlow API：`http://127.0.0.1:8501`。不要把 API 端口暴露到公网。
- 不读取、打印、复制或修改 `/opt/exhibitflow-lite/.env`，不要输出任何 API Key、Token 或 Cookie。
- 不修改 Remotion 源码，不执行 `npm install`，不运行 `git reset`、`git checkout` 或宽范围删除命令。这个 Skill 的测试目标是“外部 Agent + 已有 Remotion 渲染链路”。
- 不把未经用户提供的展会规模、排名、买家数量、成交承诺或市场结论写进文案。
- 一次只提交一个视频任务；提交前先确认没有同一会话正在运行的渲染任务。

## 文案原则

必须同时生成两套有联系但不重复的文案：

1. `script` / `voiceover`：完整、自然、适合 Edge TTS 朗读的旁白，负责讲清楚主题、受众、宣传重点和现场价值。
2. `screen_copy`：3—8 条短文案，负责标题、价值关键词、受众标签和明确数字，不能逐句复述旁白；每条不超过 28 个字符。

数字亮点必须尽量原样保留，例如 `2000+源头工厂`、`8大品类`、`60000㎡`，这样 Remotion 才能触发 0→目标值、0.5 秒完成的数字滚动动画。`screen_copy` 的 `role` 只能是 `title`、`value`、`data`、`audience` 或 `cta`。

网络检索词必须是可以被镜头拍到的具体场景或动作，并分到四个角色：`venue`、`industry`、`business`、`atmosphere`。不要把“焦虑、机会、增长”等抽象词直接作为素材检索词。

## 执行步骤

1. 先执行：

   ```bash
   curl -fsS http://127.0.0.1:8501/api/config
   ```

   确认 `topic_video.configured` 为 `true`，并根据 `topic_video.network_stock` 选择已配置的网络素材源。不要打印配置中的密钥字段。

2. 根据用户输入生成一个受约束的 `skill_plan`，至少包含：

   ```json
   {
     "plan_version": "hermes-remotion-0.1",
     "skill": {"name": "remotion-exhibition", "version": "0.1.0"},
     "agent": {"mode": "hermes-skill", "provider": "hermes", "status": "generated"},
     "voiceover": "完整旁白，多句换行",
     "screen_copy": [{"text": "展会主题", "role": "title"}],
     "visual_plan": {
       "industry": "展会所属行业",
       "video_type": "trade show promotion",
       "visual_tone": "professional and energetic",
       "groups": [
         {"role": "venue", "ratio": 0.25, "terms": ["trade show exhibition hall", "expo booth interior"]},
         {"role": "industry", "ratio": 0.30, "terms": ["具体行业生产线", "product manufacturing"]},
         {"role": "business", "ratio": 0.25, "terms": ["business meeting exhibition", "buyer supplier discussion"]},
         {"role": "atmosphere", "ratio": 0.20, "terms": ["professional trade show crowd", "exhibition networking"]}
       ]
     },
     "style": {"animation_profile": "kinetic-pop", "accent": "red-blue", "intensity": "high"},
     "copy_relation": {
       "voiceover": "完整解释主题、受众和现场价值，由 Edge TTS 朗读。",
       "screen": "提炼标题、价值关键词、受众标签和明确数据，用于 Remotion 动态文字。",
       "rule": "屏幕短文案与旁白相关但不逐句重复。"
     }
   }
   ```

3. 将 `skill_plan.voiceover` 作为 `script`，向现有异步接口提交 JSON。测试 Hermes 外部 Agent 时，`agent_mode` 必须使用 `hermes-skill`，不要使用 `internal-skill`，避免把 Hermes 和项目内部 Video Agent 混成双层调用：

   ```bash
   curl -fsS -X POST http://127.0.0.1:8501/api/tasks/creator-pipeline \
     -H 'Content-Type: application/json' \
     --data-binary @/tmp/hermes-remotion-request.json
   ```

   请求至少包含：

   ```json
   {
     "render_engine": "remotion-topic",
     "video_template": "AutoPartsKinetic",
     "agent_mode": "hermes-skill",
     "agent": {"mode": "hermes-skill", "provider": "hermes", "status": "generated"},
     "skill_plan": "上面的完整 VideoPlan 对象",
     "topic": "展会主题",
     "event_name": "展会主题",
     "theme_text": "用户提供的展会事实原文",
     "promo_focus": "想要宣传的点",
     "audience": "用户受众",
     "background_context": "想要宣传的点与用户受众，以及日期、地点、亮点等事实",
     "script": "skill_plan.voiceover",
     "screen_copy": "skill_plan.screen_copy",
     "visual_plan": "skill_plan.visual_plan",
     "highlights": ["用户明确提供的数字亮点"],
     "target_duration_seconds": 30,
     "aspect_ratio": "16:9",
     "material_source": "pexels",
     "tts_service": "edge",
     "voice": "zh-CN-XiaoxiaoNeural",
     "cta_text": "用户指定的行动引导"
   }
   ```

4. 从返回 JSON 中读取 `id`，每 5 秒轮询：

   ```bash
   curl -fsS http://127.0.0.1:8501/api/tasks/<TASK_ID>
   ```

   状态为 `succeeded` 时，返回 `result.summary.preview_url`、`result.summary.summary_markdown_url` 和 `result.summary.voiceover_script`。状态为 `failed` 时，先报告错误，不要盲目重复提交。

5. 最终报告中明确标记：`agent=Hermes`、`skill=remotion-exhibition`、`render_engine=Remotion/AutoPartsKinetic`，并说明旁白与屏幕文字是两套关联文案。不要把服务器绝对路径或密钥带回用户可见输出。

## 可复现性

保持视频主题、事实、数字亮点、时长、比例、音色和素材源不变时，Remotion 的镜头结构和动效边界稳定；Hermes 生成的旁白、短文案和检索词可能变化。若用户要求复刻同一版，应优先复用已有 `theme_text`、`script`、`screen_copy`、`visual_plan` 和 `video_project_id`。

## 验证

成功任务必须同时产生：MP4 成片、`manifest.json` 和 `generation-summary.md`。在报告中检查 `summary.render_engine` 为 `remotion-topic`，`summary.agent.mode` 为 `hermes-skill`，并确认 `summary.voiceover_script` 与 `summary.screen_copy` 都存在。
