# APES 汽配展宣传片（Remotion 参考版）

## 版本定位

这是 `auto-parts-reference-style.mp4` 对应的 Remotion 版本。它使用 React + TypeScript 编排场景，用 Remotion 的逐帧渲染输出 MP4；不使用 HyperFrames，也不使用纯 Python/FFmpeg 的文字合成链路。

核心源码：

- `src/AutoPartsKinetic.tsx`
- Composition ID：`AutoPartsKinetic`
- 画面：1920×1080，30fps
- 总时长：约 42.42 秒

## 素材与声音

视频素材来自前期网络检索并落地到 `public/assets/network/`：

- 展馆入口、展馆通道、展馆收尾
- 工厂机械与车间
- 汽配产品、会面、零部件
- 城市航拍

配音使用 `public/assets/voice/edge-01.mp3` 至 `edge-06.mp3` 的 Edge TTS 片段，按时间轴连续拼接。视频素材本身静音，因此成片不包含背景音乐。

## 场景时间轴

| 场景 | 时长 | 内容 |
| --- | ---: | --- |
| 01 | 3.50s | 汽配外贸人 / 年度必逛展会 |
| 02 | 3.80s | 全国汽配外贸人打卡点 |
| 03 | 4.60s | 汽配产业链 上海齐汇聚 |
| 04 | 4.70s | 10大汽配出海论坛 |
| 05 | 4.50s | 100强外贸人颁奖盛典 |
| 06 | 5.10s | 2000+汽配源头工厂参展 |
| 07 | 5.00s | 新工厂 新面孔 新趋势 |
| 08 | 6.00s | APES上海国际汽配展 / 日期 |
| 09 | 7.22s | 8月我们上海见 |

## 文字动画参数

### 数字滚动

带数字的文字运行 `resolveRunText()`：

- 第 3 帧开始计数
- 15 帧完成，即 0.5 秒
- 从 0 递增到目标数字
- 使用指数缓出，结尾快速稳定
- 应用到 `10大`、`100强`、`2000+`

### 从底部弹出

每行文字和每个字符都使用逐帧插值：

- 行整体从下方 72px 上移到最终位置
- 单个字符从下方 96px 弹出
- 经过约 -10px 的上弹回 overshoot 后回到 0
- 字符按 0.42 帧递进，形成快节奏的逐字出场
- 同时包含 0.62 倍 → 1.16 倍 → 0.95 倍 → 1 倍的弹性缩放
- 进入时附带轻微旋转、透明度和横向滑入

### 其他保留动画

- 标题整体淡入淡出
- 标题组弹性缩放
- 扫光带：第 10—35 帧从左向右扫过
- 指定场景闪白
- 视频素材 1.06 倍到 1.11 倍的缓慢推近与横向移动
- 结尾 CTA 持续脉冲放大

## 渲染命令

```bash
cd /Users/xianshi/Downloads/money/exhibitflow-lite/remotion_exhibition_promo
npx remotion render AutoPartsKinetic out/auto-parts-reference-style.mp4
```

单帧检查：

```bash
npx remotion still AutoPartsKinetic --frame=30 out/auto-parts-reference-still.png
```

## 稳定性检查

渲染后建议确认：

1. 数字是否在每个场景开头 0.5 秒内完成。
2. 字符是否从画面下方进入，没有顶部裁切或越过安全区。
3. Edge TTS 是否连续，且只有一条音轨。
4. 输出是否为 1920×1080、30fps，时长约 42.42 秒。
