import { useState, useEffect, useRef } from "react";
import { listen } from "@tauri-apps/api/event";
import { VoiceOverlay, type VoiceState } from "./components/VoiceOverlay";

interface StateEvent {
  state: VoiceState;
  mode?: string;
}

interface LevelEvent {
  type: string;
  level: number;
}

const isDev = import.meta.env.DEV;

// State events can occasionally be lost on the way to the hidden webview,
// leaving a stale "recording" state whose timer keeps counting. During a
// real recording the sidecar streams level events at ~10Hz; if none arrive
// for this long, the state is stale and we self-heal to hidden.
const RECORDING_LIVENESS_TIMEOUT_MS = 2000;

export default function App() {
  const [state, setState] = useState<VoiceState>("hidden");
  const [mode, setMode] = useState<string | null>(null);
  const [levels, setLevels] = useState<number[]>([]);
  const [recSeq, setRecSeq] = useState(0);
  const lastLevelRef = useRef(0);

  useEffect(() => {
    const unlistenState = listen<StateEvent>("voiceflow://state", (event) => {
      const { state: newState, mode: newMode } = event.payload;
      setState(newState);
      if (newMode) setMode(newMode);

      if (newState === "recording") {
        setLevels([]);
        // Bump the recording session sequence so the timer remounts and
        // restarts from zero on every press, even if the previous session
        // never left the "recording" state.
        setRecSeq((s) => s + 1);
        lastLevelRef.current = Date.now();
      }
    });

    const unlistenLevel = listen<LevelEvent>("voiceflow://level", (event) => {
      lastLevelRef.current = Date.now();
      const level = typeof event.payload.level === "number" ? event.payload.level : 0;
      setLevels((prev) => {
        const next = [...prev, level];
        return next.slice(-5);
      });
    });

    return () => {
      unlistenState.then((fn) => fn());
      unlistenLevel.then((fn) => fn());
    };
  }, []);

  useEffect(() => {
    if (state !== "recording") return;
    const t = setInterval(() => {
      if (Date.now() - lastLevelRef.current > RECORDING_LIVENESS_TIMEOUT_MS) {
        setState("hidden");
      }
    }, 500);
    return () => clearInterval(t);
  }, [state]);

  return (
    <div
      className="flex items-center justify-center w-full h-full"
      style={{ background: "transparent" }}
    >
      <VoiceOverlay state={state} levels={levels} recSeq={recSeq} />

      {isDev && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 flex gap-1 bg-gray-800/80 px-2 py-1 rounded-lg text-[10px] text-gray-400">
          <span>{state}</span>
          {mode && <span className="text-indigo-400">({mode})</span>}
        </div>
      )}
    </div>
  );
}
