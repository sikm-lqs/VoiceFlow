import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
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

export function VoiceOverlay({ state, levels, recSeq }: { state: VoiceState; levels: number[]; recSeq: number }) {
  return (
    <AnimatePresence>
      {state !== "hidden" && (
        <motion.div
          key="capsule"
          initial={{ opacity: 0, scale: 0.94, y: 6 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.96, y: 4 }}
          transition={{ type: "spring", stiffness: 320, damping: 26 }}
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
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={state === "recording" ? `recording-${recSeq}` : state}
              initial={{ opacity: 0, y: 5 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -5 }}
              transition={{ duration: 0.16, ease: "easeOut" }}
              style={{ display: "flex", alignItems: "center", gap: 10 }}
            >
              {state === "recording" && <RecordingView levels={levels} />}
              {state === "transcribing" && <Spinner />}
              {state === "inserted" && <CheckIcon />}
              {state === "error" && <ErrorIcon />}
              {state !== "recording" && <span>{LABEL[state]}</span>}
            </motion.div>
          </AnimatePresence>
        </motion.div>
      )}
    </AnimatePresence>
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

function RecordingView({ levels }: { levels: number[] }) {
  const seconds = useRecordingSeconds();

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
            <motion.div
              key={i}
              animate={{ height: h, opacity: 0.45 + lvl * 0.55 }}
              transition={{ duration: 0.12, ease: "easeOut" }}
              style={{
                width: 3,
                minHeight: 4,
                borderRadius: 2,
                background: ACCENT.recording,
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

function useRecordingSeconds(): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return seconds;
}

function formatSeconds(total: number): string {
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/* ---------- transcribing ---------- */

function Spinner() {
  return (
    <motion.div
      animate={{ rotate: 360 }}
      transition={{ duration: 0.9, repeat: Infinity, ease: "linear" }}
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
    <motion.span
      initial={{ scale: 0.4, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ type: "spring", stiffness: 380, damping: 20 }}
      style={{ display: "flex", alignItems: "center" }}
    >
      <Check size={17} strokeWidth={2.6} color={ACCENT.inserted} />
    </motion.span>
  );
}

function ErrorIcon() {
  return (
    <motion.span
      initial={{ scale: 0.4, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ type: "spring", stiffness: 380, damping: 20 }}
      style={{ display: "flex", alignItems: "center" }}
    >
      <AlertCircle size={17} strokeWidth={2.4} color={ACCENT.error} />
    </motion.span>
  );
}
