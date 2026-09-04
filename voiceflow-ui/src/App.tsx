import { useState, useEffect } from "react";
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

export default function App() {
  const [state, setState] = useState<VoiceState>("hidden");
  const [mode, setMode] = useState<string | null>(null);
  const [levels, setLevels] = useState<number[]>([]);

  useEffect(() => {
    const unlistenState = listen<StateEvent>("voiceflow://state", (event) => {
      const { state: newState, mode: newMode } = event.payload;
      setState(newState);
      if (newMode) setMode(newMode);

      if (newState === "recording") {
        setLevels([]);
      }
    });

    const unlistenLevel = listen<LevelEvent>("voiceflow://level", (event) => {
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

  return (
    <div
      className="flex items-center justify-center w-full h-full"
      style={{ background: "transparent" }}
    >
      <VoiceOverlay state={state} levels={levels} />

      {isDev && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 flex gap-1 bg-gray-800/80 px-2 py-1 rounded-lg text-[10px] text-gray-400">
          <span>{state}</span>
          {mode && <span className="text-indigo-400">({mode})</span>}
        </div>
      )}
    </div>
  );
}
