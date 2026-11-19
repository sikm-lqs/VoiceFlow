import { useEffect, useState } from "react";
import { Check, AlertCircle } from "lucide-react";

export type VoiceState =
  | "hidden"
  | "recording"
  | "transcribing"
  | "inserted"
  | "error";

const ACCENT: Record<Exclude<VoiceState, "hidden">, string> = {
  recording: "#f5a95c",
  transcribing: "#a78bfa",
  inserted: "#5fd39a",
  error: "#f87171",
};

const LABEL: Record<Exclude<VoiceState, "hidden">, string> = {
  recording: "聆听中",
  transcribing: "识别中",
  inserted: "已插入",
  error: "识别失败",
};

/**
 * Deliberately free of JS-driven animation libraries (framer-motion):
 * their animations are stepped by requestAnimationFrame, which the browser
 * throttles while the window is hidden. Depending on them made the capsule
 * mount at opacity 0 and stay invisible, and made AnimatePresence wait
 * forever to unmount. Visibility and layout here depend only on state.
 */
export function VoiceOverlay({
  state,
  levels,
  recSeq,
  startedAtMs,
}: {
  state: VoiceState;
  levels: number[];
  recSeq: number;
  startedAtMs: number;
}) {
  if (state === "hidden") {
    return null;
  }

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: 56,
        padding: "0 20px",
        borderRadius: 28,
        background: "rgba(22, 22, 27, 0.96)",
        border: `1px solid ${hexA(ACCENT[state], 0.35)}`,
        boxShadow: [
          "0 12px 32px rgba(0,0,0,0.35)",
          `0 0 0 1px rgba(0,0,0,0.2)`,
          `inset 0 1px 0 ${hexA(ACCENT[state], 0.12)}`,
        ].join(", "),
        fontFamily:
          '-apple-system, "Segoe UI", "Noto Sans CJK SC", "PingFang SC", "Microsoft YaHei", sans-serif',
        color: "rgba(255,255,255,0.92)",
        fontSize: 14,
        fontWeight: 500,
        whiteSpace: "nowrap",
        userSelect: "none",
      }}
    >
      <div
        key={state === "recording" ? `recording-${recSeq}` : state}
        style={{ display: "flex", alignItems: "center", gap: 10 }}
      >
        {state === "recording" && (
          <RecordingView levels={levels} startedAtMs={startedAtMs} />
        )}
        {state === "transcribing" && <Spinner />}
        {state === "inserted" && <CheckIcon />}
        {state === "error" && <ErrorIcon />}
        {state !== "recording" && <span>{LABEL[state]}</span>}
      </div>
    </div>
  );
}

function hexA(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 0xff;
  const g = (n >> 8) & 0xff;
  const b = n & 0xff;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/* ---------- recording: live level bars + timer + label ---------- */

function RecordingView({
  levels,
  startedAtMs,
}: {
  levels: number[];
  startedAtMs: number;
}) {
  const seconds = useElapsedSeconds(startedAtMs);

  const bars = [...levels];
  while (bars.length < 5) bars.unshift(0);
  const last5 = bars.slice(-5);
  const nearLimit = seconds >= 55;

  return (
    <>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 3,
          height: 20,
        }}
      >
        {last5.map((lvl, i) => {
          const h = 4 + Math.round(lvl * 16);
          return (
            <div
              key={i}
              style={{
                width: 3,
                height: h,
                borderRadius: 2,
                background: ACCENT.recording,
                opacity: 0.45 + lvl * 0.55,
                transition: "height 120ms ease-out, opacity 120ms ease-out",
              }}
            />
          );
        })}
      </div>

      <span
        style={{
          fontVariantNumeric: "tabular-nums",
          fontSize: 13,
          fontWeight: 600,
          color: nearLimit ? "#ff7b72" : "rgba(255,255,255,0.85)",
          minWidth: 32,
          textAlign: "center",
        }}
      >
        {formatSeconds(seconds)}
      </span>

      <span style={{ color: ACCENT.recording }}>{LABEL.recording}</span>
    </>
  );
}

/**
 * Elapsed time is derived from the recording start timestamp sent by Rust,
 * not from component mount time, so a stale component can never display an
 * inflated duration.
 */
function useElapsedSeconds(startedAtMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, []);
  if (!startedAtMs || startedAtMs <= 0) return 0;
  return Math.max(0, Math.floor((now - startedAtMs) / 1000));
}

function formatSeconds(total: number): string {
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/* ---------- transcribing ---------- */

function Spinner() {
  return (
    <div
      className="vf-spin"
      style={{
        width: 16,
        height: 16,
        borderRadius: "50%",
        border: "2px solid rgba(167,139,250,0.25)",
        borderTopColor: ACCENT.transcribing,
      }}
    />
  );
}

/* ---------- inserted / error ---------- */

function CheckIcon() {
  return (
    <span style={{ display: "flex", alignItems: "center" }}>
      <Check size={17} strokeWidth={2.6} color={ACCENT.inserted} />
    </span>
  );
}

function ErrorIcon() {
  return (
    <span style={{ display: "flex", alignItems: "center" }}>
      <AlertCircle size={17} strokeWidth={2.4} color={ACCENT.error} />
    </span>
  );
}
