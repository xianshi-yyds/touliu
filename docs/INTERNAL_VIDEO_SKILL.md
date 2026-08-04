# 内部展会视频 Skill

当前项目的 Remotion 动态成片可以通过内部 `exhibition-video` Skill 生成结构化 `VideoPlan`，再交给 Remotion 渲染。这个链路不会让模型临时生成 HTML、React 或 shell 代码。

```text
creator-pipeline
      ↓ agent_mode=internal-skill
内部 Video Agent
      ↓ 读取 skills/exhibition-video/SKILL.md
VideoPlan JSON
      ↓ 文案、屏幕短文案、视觉检索计划
Edge TTS + Pexels/Pixabay
      ↓ manifest
AutoPartsKinetic Remotion
      ↓
final.mp4 + generation-summary.md
```

## 调用方式

```json
{
  "kind": "creator-pipeline",
  "render_engine": "remotion-topic",
  "video_template": "AutoPartsKinetic",
  "agent_mode": "internal-skill",
  "event_name": "2026 国际智能制造展",
  "topic": "2026 国际智能制造展",
  "promo_focus": "源头工厂、方案比较和现场沟通价值",
  "audience": "制造企业负责人、采购和渠道商",
  "background_context": "想要宣传的点：源头工厂、方案比较和现场沟通价值\n用户受众：制造企业负责人、采购和渠道商",
  "highlights": ["2000+源头工厂", "8大品类"],
  "target_duration_seconds": 30,
  "aspect_ratio": "16:9",
  "material_source": "pexels",
  "tts_service": "edge",
  "voice": "zh-CN-XiaoxiaoNeural"
}
```

如果没有手动传入 `script`，内部 Agent 生成旁白；如果传入了 `script`，Skill 仍然生成关联的屏幕短文案和素材检索计划，但保留用户口播稿。

每次任务会在 `storage/video_projects/<project_id>/` 保存：

- `video-plan.json`：内部 Agent 按 Skill 生成的结构化方案；
- `manifest.json`：Remotion 实际使用的渲染输入；
- `generation-summary.md`：面向查看和审计的稳定记录；
- `final.mp4`：最终成片。

`manifest.json` 的 `agent`、`skill`、`videoPlan` 字段会记录 Skill 版本、模型提供方、回退状态和最终渲染方案。没有配置 `TEXT_LLM_API_KEY` 时，会使用同一 Skill 的规则回退方案，视频仍可继续生成。
