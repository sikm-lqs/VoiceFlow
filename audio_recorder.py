import logging
import tempfile
import threading

import numpy as np
import sounddevice as sd
from scipy.io import wavfile

logger = logging.getLogger("voice_input.audio")


class MicNotFoundError(Exception):
    pass


class AudioRecorder:
    """Audio recorder with pre-opened stream for zero-startup-delay recording.

    The audio stream runs continuously. When start() is called, the last
    pre_roll_ms of audio is taken from a fixed-size ring buffer, plus live
    audio until stop() is called. This eliminates the 50-200ms delay caused
    by opening a new PortAudio stream each time.
    """

    DEFAULT_PRE_ROLL_MS = 200

    def __init__(self, sample_rate: int = 16000, channels: int = 1,
                 max_seconds: int = 60, pre_roll_ms: int = DEFAULT_PRE_ROLL_MS):
        self.sample_rate = sample_rate
        self.channels = channels
        self.max_seconds = max_seconds
        self.pre_roll_ms = pre_roll_ms

        self._recording = False
        self._stop_event = threading.Event()
        self._max_timer = None
        self._audio_lock = threading.Lock()

        # Live mic level (0..1) with fast-attack / slow-release envelope
        self._level = 0.0
        # Set when the 60s cap triggered an automatic stop
        self.auto_stopped = False
        self.auto_wav_path: str = ""

        # Fixed-size pre-roll ring buffer, measured in SAMPLES (not blocks),
        # so the captured lead-in is exactly pre_roll_ms regardless of how
        # PortAudio chunks its callbacks.
        self._pre_roll_cap = max(int(sample_rate * pre_roll_ms / 1000), 0)
        self._pre_roll_buf = np.zeros(
            (max(self._pre_roll_cap, 1), channels), dtype=np.int16)
        self._pre_roll_pos = 0   # next write index
        self._pre_roll_len = 0   # valid samples currently in buffer
        self._frames: list[np.ndarray] = []

        # Pre-open audio stream
        self._stream: sd.InputStream | None = None
        self._open_stream()

    def _open_stream(self) -> None:
        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                callback=self._audio_callback,
            )
            self._stream.start()
            logger.info("Audio stream opened (sr=%d, pre-roll=%dms)",
                        self.sample_rate, self.pre_roll_ms)
        except sd.PortAudioError as e:
            logger.error("Failed to open audio stream: %s", e)
            self._stream = None

    def _audio_callback(self, indata: np.ndarray, frames: int,
                        time_info, status) -> None:
        if status:
            logger.warning("Audio callback status: %s", status)
        frame = indata.copy()
        with self._audio_lock:
            if self._recording:
                self._frames.append(frame)
                rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
                level = min(rms / 6000.0, 1.0)
                self._level = max(level, self._level * 0.82)
            elif self._pre_roll_cap > 0:
                self._write_pre_roll(frame)

    def _write_pre_roll(self, frame: np.ndarray) -> None:
        """Write a callback block into the fixed-size ring buffer."""
        cap = len(self._pre_roll_buf)
        n = len(frame)
        pos = self._pre_roll_pos
        end = pos + n
        if end <= cap:
            self._pre_roll_buf[pos:end] = frame
        else:
            first = cap - pos
            self._pre_roll_buf[pos:] = frame[:first]
            self._pre_roll_buf[:end - cap] = frame[first:]
        self._pre_roll_pos = end % cap
        self._pre_roll_len = min(self._pre_roll_len + n, cap)

    def _drain_pre_roll(self) -> np.ndarray:
        """Return the buffered pre-roll audio in chronological order."""
        cap = len(self._pre_roll_buf)
        length = min(self._pre_roll_len, cap)
        if length == 0:
            return np.empty((0, self.channels), dtype=np.int16)
        start = (self._pre_roll_pos - length) % cap
        if start + length <= cap:
            return self._pre_roll_buf[start:start + length].copy()
        return np.concatenate(
            (self._pre_roll_buf[start:].copy(),
             self._pre_roll_buf[:self._pre_roll_pos].copy()),
            axis=0,
        )

    def get_level(self) -> float:
        return round(min(self._level, 1.0), 3)

    def start(self) -> None:
        if self._recording:
            logger.warning("Already recording, ignoring start()")
            return
        if not self._stream:
            # Try to re-open
            self._open_stream()
            if not self._stream:
                raise MicNotFoundError("No microphone available")

        with self._audio_lock:
            pre = self._drain_pre_roll()
            self._frames = [pre] if len(pre) > 0 else []
            self._pre_roll_pos = 0
            self._pre_roll_len = 0
            self._recording = True

        self._max_timer = threading.Timer(self.max_seconds, self._auto_stop)
        self._max_timer.daemon = True
        self._max_timer.start()
        logger.info("Recording started (sr=%d, max=%ds, pre-roll=%dms/%d samples)",
                    self.sample_rate, self.max_seconds,
                    self.pre_roll_ms, len(self._frames[0]) if self._frames else 0)

    def _auto_stop(self) -> None:
        if self._recording:
            logger.info("Max duration reached, auto-stopping")
            self.auto_stopped = True
            _, wav_path = self._do_stop()
            self.auto_wav_path = wav_path

    def stop(self) -> tuple[np.ndarray, str]:
        if not self._recording:
            raise RuntimeError("Not recording, cannot stop")
        return self._do_stop()

    def _do_stop(self) -> tuple[np.ndarray, str]:
        with self._audio_lock:
            self._recording = False
            frames = self._frames
            self._frames = []
        if self._max_timer:
            self._max_timer.cancel()
            self._max_timer = None

        if not frames:
            data = np.array([], dtype=np.int16)
        else:
            data = np.concatenate(frames, axis=0).flatten()

        duration = len(data) / self.sample_rate if len(data) > 0 else 0
        logger.info("Recording stopped: %.2fs, %d samples", duration, len(data))

        wav_path = ""
        if len(data) > 0:
            wav_path = self._save_wav(data)

        return data, wav_path

    def _save_wav(self, data: np.ndarray) -> str:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        wavfile.write(tmp.name, self.sample_rate, data)
        tmp.close()
        logger.debug("WAV saved: %s", tmp.name)
        return tmp.name

    @property
    def is_recording(self) -> bool:
        return self._recording
