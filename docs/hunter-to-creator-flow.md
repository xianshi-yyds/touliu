# 找爆款 → 生视频脚本链路

## 入口与边界

找爆款员工只保留两个工作区：

- **任务**：创建检索任务、查看检索和逻辑反推进度。
- **成果**：查看已经生成的爆款逻辑成果。

它不会因为找到案例而自动创建生视频任务，也不再把“交给生视频员工”当作找爆款的步骤。

## 找爆款员工的交付物

用户在检索结果中选择一条或多条案例后，点击“生成爆款逻辑”。服务端创建独立的 `sample-analysis` 任务，后台完成后保存一条 `sample_pack` 成果：

```json
{
  "keyword": "当前检索主题",
  "platform": "douyin",
  "samples": [],
  "viral_logic": {
    "version": "1.0",
    "analysis_type": "viral_logic_timeline",
    "evidence_level": "metadata",
    "evidence_summary": "依据标题、描述和互动数据；未读取完整音视频时必须明确标记",
    "summary": "案例共同的表达逻辑",
    "audience": "主要受众",
    "core_tension": "决策阻力",
    "timeline": [
      {
        "start_sec": 0,
        "end_sec": 3,
        "phase": "hook",
        "goal": "这一段要完成什么",
        "content_role": "内容角色，不是原文",
        "voiceover_pattern": "可泛化的句式方向",
        "visual_role": "画面承载什么"
      }
    ],
    "reuse_rules": [],
    "avoid_rules": [],
    "source_sample_ids": []
  }
}
```

`timeline` 是结构合同，不是原视频逐字稿。当前检索接口通常只提供标题、描述和互动数据，因此缺少完整音频、字幕或画面时，结果会标记为 `metadata`；后续接入下载、ASR、OCR 后再升级为 `partial` 或更完整的证据等级。

## 生视频员工如何接收

进入生视频员工的**脚本**区后，用户可以在“参考案例”中选择一条找爆款成果：

1. 选择成果后，当前任务引用该成果版本和第一条代表案例。
2. 主题、受众、痛点、解决路径仍由用户确认，不被案例覆盖。
3. 点击生成文案时，把 `reference_logic` 和 `reference_artifact_id` 一起传给文案任务。
4. 文案模型只借鉴时间线的节奏和信息功能，重新生成当前展会的原创口播稿。
5. 用户确认口播稿后，继续生成语音 → 选择展会素材 → 生成可编辑字幕草稿 → 预览和保存视频创作包。

对应的任务数据关系是：

```text
search / import-links
        ↓ 用户选择案例
sample-analysis
        ↓ 服务端保存成果
sample_pack V1（viral_logic.timeline）
        ↓ 用户在脚本区主动选择
copy / creator-pipeline（引用 sample_pack V1）
        ↓ 用户确认
tts → 素材 → 字幕草稿 → 视频创作包
```

上游成果的新版本不会覆盖已经创建的下游视频任务；下游任务始终引用选择当时的成果版本。
