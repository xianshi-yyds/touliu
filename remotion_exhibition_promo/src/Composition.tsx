import { Audio, Video } from "@remotion/media";
import type { Caption } from "@remotion/captions";
import {
  AbsoluteFill,
  CalculateMetadataFunction,
  Composition,
  Easing,
  Sequence,
  staticFile,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { promoData } from "./promo-data";

type PromoProps = Record<string, unknown>;
type PromoScene = (typeof promoData.scenes)[number];

const captions = promoData.captions as readonly Caption[];

const calculateMetadata: CalculateMetadataFunction<PromoProps> = () => ({
  durationInFrames: Math.ceil(promoData.durationSeconds * promoData.fps),
  fps: promoData.fps,
  width: promoData.width,
  height: promoData.height,
});

const sceneRange = (scene: PromoScene) => ({
  from: Math.round(scene.start * promoData.fps),
  durationInFrames: Math.max(1, Math.round((scene.end - scene.start) * promoData.fps)),
});

const SceneVisual: React.FC<{ scene: PromoScene; sceneIndex: number }> = ({ scene, sceneIndex }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const duration = Math.max(1, Math.round((scene.end - scene.start) * fps));
  const asset = scene.assets[sceneIndex % scene.assets.length];
  const intro = interpolate(frame, [0, 18], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const outro = interpolate(frame, [Math.max(0, duration - 18), duration], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.65, 0, 0.84, 0),
  });

  return (
    <AbsoluteFill style={{ opacity: Math.min(intro, outro), backgroundColor: "#0b1220" }}>
      <Video
        src={staticFile(asset)}
        loop
        muted
        objectFit="cover"
        playbackRate={1.02}
        style={{
          position: "absolute",
          width: "100%",
          height: "100%",
          filter: "saturate(0.92) contrast(1.06) brightness(0.78)",
          scale: interpolate(frame, [0, duration], [1.04, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          }),
        }}
      />
      <AbsoluteFill
        style={{
          background:
            "linear-gradient(180deg, rgba(5,11,22,.56) 0%, rgba(5,11,22,.08) 42%, rgba(5,11,22,.9) 100%)",
        }}
      />
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(circle at 82% 15%, rgba(79,218,255,.32), transparent 30%), radial-gradient(circle at 12% 80%, rgba(90,66,255,.28), transparent 33%)",
          mixBlendMode: "screen",
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "flex-start",
          gap: 22,
          padding: "150px 84px 360px",
          color: "#fff",
        }}
      >
        <div
          style={{
            opacity: intro,
            translate: `0px ${interpolate(frame, [0, 18], [32, 0], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
            })}px`,
            fontSize: 30,
            fontWeight: 800,
            letterSpacing: "0.08em",
            color: "#9cecff",
          }}
        >
          {scene.kicker}
        </div>
        <h1
          style={{
            maxWidth: 900,
            margin: 0,
            opacity: intro,
            translate: `0px ${interpolate(frame, [5, 24], [48, 0], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
            })}px`,
            scale: interpolate(frame, [5, 24], [0.96, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
            }),
            fontSize: 88,
            lineHeight: 1.08,
            fontWeight: 900,
            letterSpacing: "-0.045em",
            textShadow: "0 10px 30px rgba(0,0,0,.32)",
          }}
        >
          {scene.title}
        </h1>
        <div
          style={{
            width: 96,
            height: 8,
            borderRadius: 99,
            opacity: intro,
            background: "linear-gradient(90deg, #9cecff, #7a6cff)",
          }}
        />
      </div>
      <div
        style={{
          position: "absolute",
          left: 84,
          right: 84,
          bottom: 118,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          color: "rgba(255,255,255,.82)",
          fontSize: 26,
          fontWeight: 700,
          letterSpacing: "0.04em",
        }}
      >
        <span>EXHIBITFLOW STUDIO</span>
        <span>{String(sceneIndex + 1).padStart(2, "0")} / 05</span>
      </div>
    </AbsoluteFill>
  );
};

const CaptionOverlay: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const timeMs = (frame / fps) * 1000;
  const active = captions.find((caption) => timeMs >= caption.startMs && timeMs < caption.endMs);
  const captionOpacity = active
    ? interpolate(timeMs, [active.startMs, active.startMs + 120, active.endMs - 120, active.endMs], [0, 1, 1, 0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      })
    : 0;

  return (
    <div
      style={{
        position: "absolute",
        left: 72,
        right: 72,
        bottom: 210,
        display: "flex",
        justifyContent: "center",
        opacity: captionOpacity,
      }}
    >
      <div
        style={{
          maxWidth: 900,
          padding: "18px 28px 20px",
          borderRadius: 18,
          background: "rgba(7,12,22,.72)",
          color: "#fff",
          fontSize: 43,
          lineHeight: 1.35,
          fontWeight: 850,
          textAlign: "center",
          textShadow: "0 3px 12px rgba(0,0,0,.45)",
          boxShadow: "0 16px 40px rgba(0,0,0,.18)",
        }}
      >
        {active?.text}
      </div>
    </div>
  );
};

export const ExhibitionPromo: React.FC<PromoProps> = () => {
  return (
    <AbsoluteFill style={{ backgroundColor: "#0b1220", fontFamily: "Noto Sans SC, PingFang SC, sans-serif" }}>
      <Audio src={staticFile(promoData.audio.file)} />
      {promoData.scenes.map((scene, index) => {
        const range = sceneRange(scene);
        return (
          <Sequence key={scene.id} from={range.from} durationInFrames={range.durationInFrames}>
            <SceneVisual scene={scene} sceneIndex={index} />
          </Sequence>
        );
      })}
      <CaptionOverlay />
    </AbsoluteFill>
  );
};

export const MyComposition = () => {
  return (
    <Composition
      id="ExhibitionPromo"
      component={ExhibitionPromo}
      durationInFrames={Math.ceil(promoData.durationSeconds * promoData.fps)}
      fps={promoData.fps}
      width={promoData.width}
      height={promoData.height}
      defaultProps={{}}
      calculateMetadata={calculateMetadata}
    />
  );
};
