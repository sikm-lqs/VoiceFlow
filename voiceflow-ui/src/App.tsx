import { useState, useEffect, useRef } from "react";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { VoiceOverlay, type VoiceState } from "./components/VoiceOverlay";

interface StateEvent {
  state: VoiceState;
  mode?: string;
  seq?: number;
  started_at_ms?: number;
}

interface LevelEvent {
  type: string;
  level: number;
}

interface StateSnapshot {
  state: VoiceState;
  seq: number;
  started_at_ms: number;
}

const isDev = import.meta.env.DEV;

// During a real recording the sidecar streams level events at ~10Hz. If none
// arrive for this long while we believe we are recording, the UI state is
// stale and we fall back to hidden.
const RECORDING_LIVENESS_TIMEOUT_MS = 2000;

// A level stream (re)starting after this gap marks a new recording session.
const RECORDING_SESSION_GAP_MS = 1500;

// How often we reconcile with Rust's authoritative state.
const STATE_POLL_MS = 1000;

function uiLog(message: string) {
  invoke("ui_log", { message }).catch(() => {});
}

export default function App() {
  const [state, setState] = useState<VoiceState>("hidden");
  const [mode, setMode] = useState<string | null>(null);
  const [levels, setLevels] = useState<number[]>([]);
  const [recSeq, setRecSeq] = useState(0);
  const [startedAtMs, setStartedAtMs] = useState(0);
  const lastLevelRef = useRef(0);
  const stateRef = useRef<VoiceState>("hidden");

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  // Start of a new recording session: reset the elapsed-time base and force
  // the timer component to remount. `startedAt` comes from Rust and is
  // authoritative; without it we fall back to now.
  const startSession = (startedAt?: number) => {
    setLevels([]);
    setRecSeq((s) => s + 1);
    setStartedAtMs(
      typeof startedAt === "number" && startedAt > 0 ? startedAt : Date.now(),
    );
    lastLevelRef.current = Date.now();
  };

  useEffect(() => {
    const unlistenState = listen<StateEvent>("voiceflow://state", (event) => {
      const p = event.payload;
      uiLog(`state=${p.state} seq=${p.seq ?? "-"} started=${p.started_at_ms ?? "-"}`);
      setState(p.state);
      if (p.mode) setMode(p.mode);

      if (p.state === "recording") {
        startSession(p.started_at_ms);
      }
    });

    const unlistenLevel = listen<LevelEvent>("voiceflow://level", (event) => {
      const now = Date.now();
      const gap = now - lastLevelRef.current;
      lastLevelRef.current = now;

      // Level events only flow during a real recording, so a resumed stream
      // means a new session even if the state event was dropped.
      if (gap > RECORDING_SESSION_GAP_MS) {
        uiLog(`level stream resumed after ${gap}ms -> new session`);
        startSession();
      }

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

  // Reconcile with Rust's authoritative state. Push events can be dropped
  // while the webview is hidden; this request/response check (polled, plus
  // immediately when the window becomes visible) cannot be.
  useEffect(() => {
    let stopped = false;

    const reconcile = async () => {
      try {
        const snap = await invoke<StateSnapshot>("get_state");
        if (stopped) return;
        if (snap.state !== stateRef.current) {
          uiLog(`reconcile ${stateRef.current} -> ${snap.state}`);
          setState(snap.state);
          if (snap.state === "recording") {
            startSession(snap.started_at_ms);
          }
        }
      } catch {
        /* ignore transient IPC failures */
      }
    };

    const timer = setInterval(reconcile, STATE_POLL_MS);
    const onVisible = () => {
      if (!document.hidden) reconcile();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);

    return () => {
      stopped = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, []);

  useEffect(() => {
    if (state !== "recording") return;
    const t = setInterval(() => {
      if (Date.now() - lastLevelRef.current > RECORDING_LIVENESS_TIMEOUT_MS) {
        uiLog("watchdog: stale recording -> hidden");
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
      <VoiceOverlay
        state={state}
        levels={levels}
        recSeq={recSeq}
        startedAtMs={startedAtMs}
      />

      {isDev && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 flex gap-1 bg-gray-800/80 px-2 py-1 rounded-lg text-[10px] text-gray-400">
          <span>{state}</span>
          {mode && <span className="text-indigo-400">({mode})</span>}
        </div>
      )}
    </div>
  );
}
