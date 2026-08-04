import { Audio, Video } from "@remotion/media";
import {
  AbsoluteFill,
  Composition,
  Easing,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
} from "remotion";

const FPS = 30;
const WIDTH = 1920;
const HEIGHT = 1080;
const DEFAULT_TOTAL_SECONDS = 42.42;
// Numeric runs begin counting as soon as their scene starts and finish at
// exactly 0.5s, matching the requested kinetic-typography timing.
const COUNTER_START_FRAME = 0;

export type TextRun = {
  text: string;
  color: string;
  counter?: number;
  suffix?: string;
};

export type SceneLine = {
  runs: TextRun[];
  size?: number;
};

export type FontStyle = "impact" | "tech" | "editorial" | "mono";
export type TransitionType = "cut" | "fade" | "wipe" | "flash";

export type TransitionConfig = {
  type: TransitionType;
  durationSeconds?: number;
};

export type BgmConfig = {
  file: string;
  name?: string;
  duration?: number;
  volume?: number;
  loop?: boolean;
};

export type Scene = {
  id: string;
  start: number;
  duration: number;
  video: string;
  lines: SceneLine[];
  align: "left" | "center" | "right";
  top: number;
  accent: string;
  subline?: string;
  opening?: boolean;
  final?: boolean;
  flash?: boolean;
  visualSource?: "broll" | "avatar";
};

export type DigitalHumanConfig = {
  enabled?: boolean;
  status?: string;
  mode?: "montage" | "avatar" | "hybrid";
  file?: string;
  layout?: "none" | "fullscreen" | "circle-pip";
  position?: string;
  shape?: string;
  visibleSceneIds?: string[];
};

export type VoiceSegment = {
  file: string;
  start: number;
  duration: number;
};

export type AutoPartsManifest = {
  version?: number;
  template?: string;
  projectId?: string;
  title?: string;
  brand?: string;
  brandMark?: string;
  fps?: number;
  width?: number;
  height?: number;
  aspectRatio?: string;
  durationSeconds?: number;
  scenes: Scene[];
  voiceSegments?: VoiceSegment[];
  fontStyle?: FontStyle;
  transition?: TransitionConfig | TransitionType;
  bgm?: BgmConfig | null;
  digitalHuman?: DigitalHumanConfig;
  productionPlan?: Record<string, unknown>;
};

export type AutoPartsKineticProps = {
  manifest?: AutoPartsManifest;
};

const white = "#ffffff";
const red = "#f0332b";
const blue = "#1f64dc";
const dark = "#102448";

const FONT_CONFIG: Record<FontStyle, { family: string; weight: number; letterSpacing: string }> = {
  impact: {
    family: '"Arial Black", Arial, "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif',
    weight: 900,
    letterSpacing: "-0.045em",
  },
  tech: {
    family: '"Arial Narrow", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif',
    weight: 800,
    letterSpacing: "-0.02em",
  },
  editorial: {
    family: '"Noto Serif SC", "Songti SC", STSong, SimSun, serif',
    weight: 800,
    letterSpacing: "-0.025em",
  },
  mono: {
    family: '"SFMono-Regular", "Roboto Mono", "Noto Sans Mono CJK SC", monospace',
    weight: 800,
    letterSpacing: "-0.015em",
  },
};

const defaultScenes: Scene[] = [
  {
    id: "01",
    start: 0,
    duration: 3.5,
    video: "assets/network/venue/pexels-c19eebe78961.mp4",
    align: "left",
    top: 365,
    accent: blue,
    opening: true,
    lines: [
      { runs: [{ text: "汽配外贸人", color: dark }], size: 108 },
      { runs: [{ text: "年度必逛展会", color: blue }], size: 112 },
    ],
    subline: "2026.08.05—07  /  国家会展中心（上海）",
  },
  {
    id: "02",
    start: 3.25,
    duration: 3.8,
    video: "assets/network/venue/pexels-a3015e0a7f4f.mp4",
    align: "center",
    top: 555,
    accent: blue,
    flash: true,
    lines: [{ runs: [{ text: "全国汽配外贸人打卡点", color: white }], size: 94 }],
  },
  {
    id: "03",
    start: 6.8,
    duration: 4.6,
    video: "assets/network/factory/pexels-e0fb5e8d92e1.mp4",
    align: "center",
    top: 552,
    accent: blue,
    lines: [
      {
        runs: [
          { text: "汽配产业链", color: white },
          { text: " 上海齐汇聚", color: blue },
        ],
        size: 92,
      },
    ],
  },
  {
    id: "04",
    start: 11.15,
    duration: 4.7,
    video: "assets/network/factory/pexels-97d8a79856e3.mp4",
    align: "center",
    top: 560,
    accent: blue,
    flash: true,
    lines: [
      {
        runs: [
          { text: "10", color: blue, counter: 10, suffix: "大" },
          { text: "汽配出海论坛", color: white },
        ],
        size: 94,
      },
    ],
  },
  {
    id: "05",
    start: 15.6,
    duration: 4.5,
    video: "assets/network/product/pexels-f05cb1b46681.mp4",
    align: "center",
    top: 565,
    accent: red,
    flash: true,
    lines: [
      {
        runs: [
          { text: "100", color: red, counter: 100, suffix: "强" },
          { text: " 外贸人颁奖盛典", color: white },
        ],
        size: 94,
      },
    ],
  },
  {
    id: "06",
    start: 19.85,
    duration: 5.1,
    video: "assets/network/product/pexels-75bb69b6e1b4.mp4",
    align: "center",
    top: 565,
    accent: blue,
    lines: [
      {
        runs: [
          { text: "2000", color: blue, counter: 2000, suffix: "+" },
          { text: " 汽配源头工厂参展", color: white },
        ],
        size: 94,
      },
    ],
  },
  {
    id: "07",
    start: 24.7,
    duration: 5,
    video: "assets/network/product/pexels-7c1f3c3d89df.mp4",
    align: "center",
    top: 565,
    accent: red,
    flash: true,
    lines: [
      {
        runs: [
          { text: "新工厂", color: red },
          { text: " 新面孔", color: white },
          { text: " 新趋势", color: red },
        ],
        size: 94,
      },
    ],
  },
  {
    id: "08",
    start: 29.45,
    duration: 6,
    video: "assets/network/city/pexels-715cb627082d.mp4",
    align: "center",
    top: 530,
    accent: white,
    lines: [
      { runs: [{ text: "APES上海国际汽配展", color: white }], size: 88 },
      { runs: [{ text: "2026年8月5日—7日", color: white }], size: 76 },
    ],
  },
  {
    id: "09",
    start: 35.2,
    duration: 7.22,
    video: "assets/network/venue/pexels-21261a1c4467.mp4",
    align: "left",
    top: 420,
    accent: blue,
    final: true,
    lines: [
      {
        runs: [
          { text: "8月", color: blue },
          { text: "我们上海见", color: dark },
        ],
        size: 112,
      },
    ],
    subline: "SEE YOU IN SHANGHAI  /  THIS AUGUST",
  },
];

const DEFAULT_MANIFEST: AutoPartsManifest = {
  version: 1,
  template: "AutoPartsKinetic",
  title: "APES上海国际汽配展",
  brand: "上海国际汽配展",
  brandMark: "APES",
  fps: FPS,
  width: WIDTH,
  height: HEIGHT,
  durationSeconds: DEFAULT_TOTAL_SECONDS,
  fontStyle: "impact",
  transition: { type: "cut", durationSeconds: 0.42 },
  bgm: null,
  scenes: defaultScenes,
  voiceSegments: [
    { file: "assets/voice/edge-01.mp3", start: 0, duration: 6.792 },
    { file: "assets/voice/edge-02.mp3", start: 6.792, duration: 7.224 },
    { file: "assets/voice/edge-03.mp3", start: 14.016, duration: 6.312 },
    { file: "assets/voice/edge-04.mp3", start: 20.328, duration: 6.096 },
    { file: "assets/voice/edge-05.mp3", start: 26.424, duration: 5.976 },
    { file: "assets/voice/edge-06.mp3", start: 32.4, duration: 8.52 },
  ],
};

const clamp = {
  extrapolateLeft: "clamp" as const,
  extrapolateRight: "clamp" as const,
};

const easeOut = Easing.bezier(0.16, 1, 0.3, 1);
const easeIn = Easing.bezier(0.65, 0, 0.84, 0);
const easePop = Easing.bezier(0.34, 1.56, 0.64, 1);

const normaliseFontStyle = (value?: string): FontStyle => {
  return value === "tech" || value === "editorial" || value === "mono" ? value : "impact";
};

const normaliseTransition = (input?: TransitionConfig | TransitionType): TransitionConfig => {
  const raw: Partial<TransitionConfig> = typeof input === "string" ? { type: input } : input || {};
  const type: TransitionType = raw.type === "fade" || raw.type === "wipe" || raw.type === "flash" ? raw.type : "cut";
  const durationSeconds = Math.max(0.16, Math.min(1.2, Number(raw.durationSeconds) || 0.42));
  return { type, durationSeconds };
};

const normalizeManifest = (input?: AutoPartsManifest): AutoPartsManifest => {
  if (!input?.scenes?.length) {
    return DEFAULT_MANIFEST;
  }
  return {
    ...DEFAULT_MANIFEST,
    ...input,
    fps: input.fps || FPS,
    width: input.width || WIDTH,
    height: input.height || HEIGHT,
    fontStyle: normaliseFontStyle(input.fontStyle),
    transition: normaliseTransition(input.transition),
    scenes: input.scenes,
    voiceSegments: input.voiceSegments || [],
    bgm: input.bgm || null,
  };
};

const resolveRunText = (run: TextRun, frame: number, fps: number): string => {
  if (run.counter === undefined) {
    return run.text;
  }
  const counterDurationFrames = Math.round(fps * 0.5);
  const value = Math.round(
    interpolate(frame, [COUNTER_START_FRAME, COUNTER_START_FRAME + counterDurationFrames], [0, run.counter], {
      ...clamp,
      // Cubic-out reaches the exact target on the 0.5s end frame; an
      // exponential curve would leave values such as 1998+ visible there.
      easing: Easing.out(Easing.cubic),
    }),
  );
  return String(value) + (run.suffix ?? "");
};

const ReferenceBrand: React.FC<{ brand: string; brandMark?: string; light?: boolean; fontStyle?: FontStyle }> = ({ brand, brandMark, light, fontStyle = "impact" }) => {
  const font = FONT_CONFIG[fontStyle];
  return (
    <div
      style={{
        position: "absolute",
        top: 64,
        left: 84,
        display: "flex",
        alignItems: "baseline",
        gap: 13,
        color: light ? white : dark,
        opacity: 0.95,
        fontFamily: font.family,
        fontSize: 25,
        fontWeight: font.weight,
        letterSpacing: "0.06em",
      }}
    >
      <span style={{ fontSize: 36, fontStyle: "italic", letterSpacing: "-0.08em" }}>{brandMark || "EXPO"}</span>
      <span>{brand}</span>
    </div>
  );
};

const AnimatedLine: React.FC<{
  line: SceneLine;
  frame: number;
  lineIndex: number;
  align: Scene["align"];
  fps: number;
  fontStyle: FontStyle;
  accent: string;
}> = ({ line, frame, lineIndex, align, fps, fontStyle, accent }) => {
  const font = FONT_CONFIG[fontStyle];
  let characterIndex = 0;
  const lineStart = 3 + lineIndex * 4;
  const reveal = interpolate(frame, [lineStart, lineStart + 15], [0, 1], {
    ...clamp,
    easing: easeOut,
  });
  const translateY = interpolate(frame, [lineStart, lineStart + 15], [72, 0], {
    ...clamp,
    easing: easePop,
  });
  const textAlign = align === "center" ? "center" : align === "right" ? "right" : "left";
  const resolvedText = line.runs.map((run) => resolveRunText(run, frame, fps)).join("");
  const sweepAccent = fontStyle === "tech" ? "#00f0ff" : fontStyle === "editorial" ? "#ffdf00" : fontStyle === "mono" ? "#9cecff" : accent;
  const sweepCenter = interpolate(frame, [lineStart + 7, lineStart + 36], [-0.12, 1.12], {
    ...clamp,
    easing: Easing.inOut(Easing.cubic),
  });
  const sweepBandWidth = 0.14;
  const coreBandWidth = 0.045;
  const makeSweepClip = (bandWidth: number) => {
    const left = (sweepCenter - bandWidth / 2) * 100;
    const right = (1 - sweepCenter - bandWidth / 2) * 100;
    const topLeft = left - 2;
    const topRight = 100 - right + 2;
    const bottomLeft = left + 3;
    const bottomRight = 100 - right + 7;
    return `polygon(${topLeft}% 0%, ${topRight}% 0%, ${bottomRight}% 100%, ${bottomLeft}% 100%)`;
  };
  const sweepClip = makeSweepClip(sweepBandWidth);
  const coreClip = makeSweepClip(coreBandWidth);
  const sweepOpacity = interpolate(frame, [lineStart + 5, lineStart + 10, lineStart + 29, lineStart + 38], [0, 0.96, 0.82, 0], clamp);

  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        overflow: "hidden",
        clipPath: "inset(0 " + (100 - reveal * 100) + "% 0 0)",
        opacity: interpolate(frame, [lineStart, lineStart + 4], [0, 1], clamp),
        translate: "0px " + translateY + "px",
        textAlign,
      }}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "nowrap",
          justifyContent: align === "center" ? "center" : align === "right" ? "flex-end" : "flex-start",
          alignItems: "baseline",
          whiteSpace: "pre",
          fontSize: line.size ?? 94,
          lineHeight: 1.04,
          fontFamily: font.family,
          fontWeight: font.weight,
          letterSpacing: font.letterSpacing,
        }}
      >
        {line.runs.flatMap((run) => {
          const text = resolveRunText(run, frame, fps);
          return Array.from(text).map((character) => {
            const index = characterIndex;
            characterIndex += 1;
            const localFrame = frame - lineStart - index * 0.42;
            const fromX = align === "right" ? 32 : -32;
            const x = interpolate(localFrame, [0, 12], [fromX, 0], {
              ...clamp,
              easing: easeOut,
            });
            const y = interpolate(localFrame, [0, 7, 15], [96, -10, 0], {
              ...clamp,
              easing: easePop,
            });
            const scale = interpolate(localFrame, [0, 4, 9, 15], [0.62, 1.16, 0.95, 1], {
              ...clamp,
              easing: easePop,
            });
            const rotate = interpolate(localFrame, [0, 12], [align === "right" ? 5 : -5, 0], {
              ...clamp,
              easing: easeOut,
            });
            const opacity = interpolate(localFrame, [0, 2.5], [0, 1], clamp);

            return (
              <span
                key={run.text + "-" + index}
                style={{
                  display: "inline-block",
                  color: run.color,
                  opacity,
                  scale,
                  translate: x + "px " + y + "px",
                  rotate: rotate + "deg",
                  WebkitTextStroke: "1.5px rgba(3, 13, 36, 0.58)",
                  textShadow: "0 3px 0 rgba(3, 13, 36, 0.54), 0 8px 16px rgba(0, 0, 0, 0.34)",
                }}
              >
                {character === " " ? "\u00a0" : character}
              </span>
            );
          });
        })}
      </div>
      <div
        aria-hidden="true"
        style={{
          position: "absolute",
          inset: 0,
          overflow: "hidden",
          fontSize: line.size ?? 94,
          lineHeight: 1.04,
          fontFamily: font.family,
          fontWeight: font.weight,
          letterSpacing: font.letterSpacing,
          textAlign,
          whiteSpace: "pre",
          color: sweepAccent,
          clipPath: sweepClip,
          filter: `drop-shadow(0 0 10px ${sweepAccent})`,
          opacity: sweepOpacity * 0.76,
          textShadow: `0 0 16px ${sweepAccent}`,
          mixBlendMode: "screen",
          pointerEvents: "none",
        }}
      >
        {resolvedText}
      </div>
      <div
        aria-hidden="true"
        style={{
          position: "absolute",
          inset: 0,
          overflow: "hidden",
          fontSize: line.size ?? 94,
          lineHeight: 1.04,
          fontFamily: font.family,
          fontWeight: font.weight,
          letterSpacing: font.letterSpacing,
          textAlign,
          whiteSpace: "pre",
          color: "#ffffff",
          clipPath: coreClip,
          filter: "drop-shadow(0 0 8px rgba(255,255,255,.92))",
          opacity: sweepOpacity,
          mixBlendMode: "screen",
          pointerEvents: "none",
        }}
      >
        {resolvedText}
      </div>
    </div>
  );
};

const Headline: React.FC<{
  scene: Scene;
  frame: number;
  fps: number;
  width: number;
  height: number;
  brand: string;
  brandMark?: string;
  fontStyle: FontStyle;
}> = ({ scene, frame, fps, width, height, fontStyle }) => {
  const font = FONT_CONFIG[fontStyle];
  const fadeIn = interpolate(frame, [0, 8], [0, 1], { ...clamp, easing: easeOut });
  const fadeOut = interpolate(frame, [scene.duration * fps - 10, scene.duration * fps], [1, 0], {
    ...clamp,
    easing: easeIn,
  });
  const opacity = Math.min(fadeIn, fadeOut);
  const moveY = interpolate(frame, [0, 18], [24, 0], { ...clamp, easing: easeOut });
  const groupScale = interpolate(frame, [0, 8, 17], [0.9, 1.07, 1], { ...clamp, easing: easePop });
  const pulse = scene.final ? 1 + Math.sin(Math.max(frame - 35, 0) / 10) * 0.026 : 1;
  const left = scene.align === "center" ? "50%" : scene.align === "right" ? "auto" : 112;
  const right = scene.align === "right" ? 112 : "auto";
  const translateX = scene.align === "center" ? "-50%" : "0%";
  const baseWidth = scene.align === "center" ? 1660 : 1380;
  const portrait = height > width * 1.1;
  const availableWidth = portrait ? Math.max(720, width - 72) : Math.min(baseWidth, width - 224);
  const layoutScale = Math.min(1, availableWidth / baseWidth);
  const top = portrait ? Math.round((scene.top / HEIGHT) * height * 0.96) : scene.top;
  const transformOrigin = scene.align === "center" ? "50% 0%" : scene.align === "right" ? "100% 0%" : "0% 0%";

  return (
    <div
      style={{
        position: "absolute",
        top,
        left,
        right,
        width: baseWidth,
        translate: translateX + " " + moveY + "px",
        scale: groupScale * pulse * layoutScale,
        transformOrigin,
        opacity,
        display: "flex",
        flexDirection: "column",
        alignItems: scene.align === "center" ? "center" : scene.align === "right" ? "flex-end" : "flex-start",
        gap: scene.lines.length > 1 ? 3 : 0,
        fontFamily: font.family,
      }}
    >
      <div style={{ position: "relative", width: "100%" }}>
        {scene.lines.map((line, index) => (
          <AnimatedLine key={scene.id + "-line-" + index} line={line} frame={frame} lineIndex={index} align={scene.align} fps={fps} fontStyle={fontStyle} accent={scene.accent} />
        ))}
      </div>
      {scene.subline ? (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 14,
            marginTop: 24,
            color: scene.opening || scene.final ? dark : white,
            opacity: interpolate(frame, [18, 30], [0, 1], { ...clamp, easing: easeOut }),
            fontSize: 27,
            fontWeight: 700,
            letterSpacing: "0.05em",
            textShadow: scene.opening || scene.final ? "none" : "0 3px 9px rgba(0,0,0,.6)",
          }}
        >
          <span style={{ width: 66, height: 5, backgroundColor: scene.accent }} />
          {scene.subline}
        </div>
      ) : null}
    </div>
  );
};

const SceneLayer: React.FC<{
  scene: Scene;
  index: number;
  fps: number;
  width: number;
  height: number;
  brand: string;
  brandMark?: string;
  fontStyle: FontStyle;
  digitalHuman?: DigitalHumanConfig;
}> = ({ scene, index, fps, width, height, brand, brandMark, fontStyle, digitalHuman }) => {
  const frame = useCurrentFrame();
  const direction = index % 2 === 0 ? -1 : 1;
  const videoScale = interpolate(frame, [0, scene.duration * fps], [1.06, 1.11], clamp);
  const videoX = interpolate(frame, [0, scene.duration * fps], [direction * 16, direction * -14], clamp);
  const videoY = interpolate(frame, [0, scene.duration * fps], [index % 2 ? -4 : 3, 0], clamp);
  const flashOpacity = scene.flash
    ? interpolate(frame, [0, 2, 7, 14], [0, 0.72, 0.04, 0], clamp)
    : 0;
  const wash = scene.opening || scene.final
    ? "linear-gradient(90deg, rgba(231,244,255,.88) 0%, rgba(231,244,255,.46) 34%, rgba(231,244,255,0) 72%)"
    : "linear-gradient(180deg, rgba(0,0,0,.06) 0%, rgba(0,0,0,.22) 100%), radial-gradient(circle at 50% 53%, rgba(0,0,0,.24) 0%, rgba(0,0,0,0) 57%)";
  const avatarActive = Boolean(
    digitalHuman?.enabled &&
    digitalHuman.file &&
    digitalHuman.status === "completed" &&
    (scene.visualSource === "avatar" || digitalHuman.visibleSceneIds?.includes(scene.id)),
  );
  const avatarFullscreen = avatarActive && digitalHuman?.layout === "fullscreen";
  const avatarPip = avatarActive && digitalHuman?.layout === "circle-pip";
  const pipSize = height > width * 1.1 ? Math.round(width * 0.27) : Math.round(width * 0.16);
  const pipInset = Math.round(Math.min(width, height) * 0.055);

  return (
    <AbsoluteFill style={{ overflow: "hidden", backgroundColor: "#0b1326" }}>
      {!avatarFullscreen ? (
        <Video
          src={staticFile(scene.video)}
          loop
          muted
          playbackRate={1.08}
          objectFit="cover"
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            scale: videoScale,
            translate: videoX + "px " + videoY + "px",
            filter: "saturate(.96) contrast(1.04) brightness(.9)",
          }}
        />
      ) : null}
      {avatarFullscreen ? (
        <Video
          src={staticFile(digitalHuman!.file!)}
          trimBefore={Math.round(scene.start * fps)}
          muted
          objectFit="cover"
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%", filter: "saturate(1.02) contrast(1.03)" }}
        />
      ) : null}
      <AbsoluteFill style={{ background: wash }} />
      {avatarPip ? (
        <div
          style={{
            position: "absolute",
            top: pipInset,
            right: pipInset,
            width: pipSize,
            height: pipSize,
            overflow: "hidden",
            borderRadius: "50%",
            border: `${Math.max(5, Math.round(pipSize * 0.022))}px solid rgba(255,255,255,.94)`,
            boxShadow: "0 16px 42px rgba(0,0,0,.38), 0 0 0 3px rgba(31,100,220,.6)",
            backgroundColor: "#e8eef7",
            zIndex: 4,
          }}
        >
          <Video
            src={staticFile(digitalHuman!.file!)}
            trimBefore={Math.round(scene.start * fps)}
            muted
            objectFit="cover"
            style={{ width: "100%", height: "100%", scale: 1.04 }}
          />
        </div>
      ) : null}
      {scene.opening || scene.final ? <ReferenceBrand brand={brand} brandMark={brandMark} light={false} fontStyle={fontStyle} /> : null}
      <Headline scene={scene} frame={frame} fps={fps} width={width} height={height} brand={brand} brandMark={brandMark} fontStyle={fontStyle} />
      {flashOpacity > 0 ? (
        <AbsoluteFill
          style={{
            opacity: flashOpacity,
            background: index % 2 === 0 ? "rgba(255,246,219,.92)" : "rgba(225,242,255,.9)",
            mixBlendMode: "screen",
            pointerEvents: "none",
          }}
        />
      ) : null}
    </AbsoluteFill>
  );
};

const SceneTransition: React.FC<{
  startFrame: number;
  durationFrames: number;
  type: TransitionType;
  accent: string;
  index: number;
}> = ({ startFrame, durationFrames, type, accent, index }) => {
  const frame = useCurrentFrame();
  const localFrame = frame - startFrame;
  if (type === "cut" || localFrame < 0 || localFrame > durationFrames) return null;
  const midpoint = durationFrames / 2;

  if (type === "fade") {
    const opacity = interpolate(localFrame, [0, midpoint, durationFrames], [0, 0.92, 0], {
      ...clamp,
      easing: easeIn,
    });
    return <AbsoluteFill style={{ opacity, backgroundColor: "#071326", zIndex: 20, pointerEvents: "none" }} />;
  }

  if (type === "flash") {
    const opacity = interpolate(localFrame, [0, durationFrames * 0.22, durationFrames * 0.48, durationFrames], [0, 0.88, 0.18, 0], {
      ...clamp,
      easing: easeOut,
    });
    return (
      <AbsoluteFill
        style={{
          opacity,
          background: index % 2 === 0 ? "rgba(255,255,255,.98)" : `linear-gradient(105deg, rgba(255,255,255,.98), ${accent}, rgba(255,255,255,.82))`,
          mixBlendMode: "screen",
          zIndex: 20,
          pointerEvents: "none",
        }}
      />
    );
  }

  // The wipe briefly covers the cut with the active accent and then reveals
  // the incoming shot from left to right. It is intentionally frame-driven so
  // seeking and server-side rendering produce the same result.
  const coverProgress = interpolate(localFrame, [0, durationFrames * 0.52], [0, 1], {
    ...clamp,
    easing: easeOut,
  });
  const revealOpacity = interpolate(localFrame, [durationFrames * 0.48, durationFrames], [1, 0], {
    ...clamp,
    easing: easeIn,
  });
  return (
    <AbsoluteFill
      style={{
        opacity: revealOpacity,
        background: `linear-gradient(105deg, ${accent}, rgba(255,255,255,.94) 48%, ${accent})`,
        clipPath: `inset(0 ${100 - coverProgress * 100}% 0 0)`,
        zIndex: 20,
        pointerEvents: "none",
      }}
    />
  );
};

const AutoPartsKineticVideo: React.FC<AutoPartsKineticProps> = ({ manifest: rawManifest }) => {
  const manifest = normalizeManifest(rawManifest);
  const fps = manifest.fps || FPS;
  const width = manifest.width || WIDTH;
  const height = manifest.height || HEIGHT;
  const brand = manifest.brand || manifest.title || "主题展会";
  const fontStyle = normaliseFontStyle(manifest.fontStyle);
  const transition = normaliseTransition(manifest.transition);
  const transitionFrames = Math.max(1, Math.round((transition.durationSeconds || 0.42) * fps));
  const durationFrames = Math.max(1, Math.ceil((manifest.durationSeconds || DEFAULT_TOTAL_SECONDS) * fps));
  return (
    <AbsoluteFill style={{ backgroundColor: "#0b1326" }}>
      {manifest.scenes.map((scene, index) => (
        <Sequence key={scene.id} from={Math.round(scene.start * fps)} durationInFrames={Math.ceil(scene.duration * fps)}>
          <SceneLayer scene={scene} index={index} fps={fps} width={width} height={height} brand={brand} brandMark={manifest.brandMark} fontStyle={fontStyle} digitalHuman={manifest.digitalHuman} />
        </Sequence>
      ))}
      {manifest.scenes.slice(1).map((scene, index) => (
        <SceneTransition
          key={scene.id + "-transition"}
          startFrame={Math.round(scene.start * fps) - Math.floor(transitionFrames / 2)}
          durationFrames={transitionFrames}
          type={transition.type}
          accent={scene.accent || (index % 2 ? red : blue)}
          index={index}
        />
      ))}
      {(manifest.voiceSegments || []).map((voice) => (
        <Sequence key={voice.file + voice.start} from={Math.round(voice.start * fps)} durationInFrames={Math.ceil(voice.duration * fps)}>
          <Audio src={staticFile(voice.file)} />
        </Sequence>
      ))}
      {manifest.bgm?.file ? (
        <Sequence durationInFrames={durationFrames}>
          <Audio
            src={staticFile(manifest.bgm.file)}
            loop={manifest.bgm.loop !== false}
            volume={() => Math.max(0.04, Math.min(0.32, Number(manifest.bgm?.volume) || 0.16))}
          />
        </Sequence>
      ) : null}
    </AbsoluteFill>
  );
};

export const AutoPartsKinetic = () => {
  return (
    <Composition
      id="AutoPartsKinetic"
      component={AutoPartsKineticVideo}
      durationInFrames={Math.ceil(DEFAULT_TOTAL_SECONDS * FPS)}
      fps={FPS}
      width={WIDTH}
      height={HEIGHT}
      defaultProps={{ manifest: DEFAULT_MANIFEST }}
      calculateMetadata={({ props }) => {
        const manifest = normalizeManifest((props as AutoPartsKineticProps).manifest);
        const fps = manifest.fps || FPS;
        const durationSeconds = manifest.durationSeconds || DEFAULT_TOTAL_SECONDS;
        return {
          durationInFrames: Math.max(1, Math.ceil(durationSeconds * fps)),
          fps,
          width: manifest.width || WIDTH,
          height: manifest.height || HEIGHT,
        };
      }}
    />
  );
};
