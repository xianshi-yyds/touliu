# Remotion video

<p align="center">
  <a href="https://github.com/remotion-dev/logo">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://github.com/remotion-dev/logo/raw/main/animated-logo-banner-dark.apng">
      <img alt="Animated Remotion Logo" src="https://github.com/remotion-dev/logo/raw/main/animated-logo-banner-light.gif">
    </picture>
  </a>
</p>

这是 ExhibitFlow Lite 的 Remotion 展会宣传片工程。它不会把网络视频 URL 直接写进成片，而是先通过项目现有的 Pexels 检索适配器搜索、去重、按视觉角色配额下载素材，再复制到 `public/assets/network/`，这样渲染结果可复现。

默认宣传主题是“2026 国际新能源产业展”，配音使用项目现有的 Qwen TTS，并自动回退到 Edge TTS。文案、素材搜索词、来源记录和字幕时间轴会写入 `public/promo-manifest.json`。

## Commands

**Install Dependencies**

```console
npm i
```

**Prepare network assets and voiceover**

从仓库根目录的 `.env` 读取 `PEXELS_API_KEY`、`DASHSCOPE_API_KEY` 等配置：

```console
npm run prepare:promo
```

**Start Preview**

```console
npm run dev
```

**Render video**

```console
npm run render:promo
```

**Preview in Studio**

```console
npm run dev
```

**Upgrade Remotion**

```console
npx remotion upgrade
```

## Docs

Get started with Remotion by reading the [fundamentals page](https://www.remotion.dev/docs/the-fundamentals).

## Help

We provide help on our [Discord server](https://discord.gg/6VzzNDwUwV).

## Issues

Found an issue with Remotion? [File an issue here](https://github.com/remotion-dev/remotion/issues/new).

## License

Note that for some entities a company license is needed. [Read the terms here](https://github.com/remotion-dev/remotion/blob/main/LICENSE.md).
