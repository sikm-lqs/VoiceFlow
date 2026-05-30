import logging
import tempfile
import threading
from collections import deque

import numpy as np
import sounddevice as sd
from scipy.io import wavfile

logger = logging.getLogger("voice_input.audio")


class MicNotFoundError(Exception):
    pass


class AudioRecorder:
    """Audio recorder with pre-opened stream for zero-startup-delay recording.

    The audio stream runs continuously. When start() is called, frames are
    collected from a pre-roll ring buffer (last ~300ms) plus live audio until
    stop() is called. This eliminates the 50-200ms delay caused by opening
    a new PortAudio stream each time.
    """

    # Pre-roll buffer keeps ~200ms of audio before start() is called.
    PRE_ROLL_MS = 200

    def __init__(self, sample_rate: int = 16000, channels: int = 1,
                 max_seconds: int = 60):
        self.sample_rate = sample_rate
        self.channels = channels
        self.max_seconds = max_seconds

        self._recording = False
        self._stop_event = threading.Event()
        self._max_timer = None

        # Pre-roll ring buffer (always collecting)
        pre_roll_frames = int(sample_rate * self.PRE_ROLL_MS / 1000)
        self._pre_roll: deque[np.ndarray] = deque(maxlen=max(pre_roll_frames, 1))
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
                        self.sample_rate, self.PRE_ROLL_MS)
        except sd.PortAudioError as e:
            logger.error("Failed to open audio stream: %s", e)
            self._stream = None

    def _audio_callback(self, indata: np.ndarray, frames: int,
                        time_info, status) -> None:
        if status:
            logger.warning("Audio callback status: %s", status)
        frame = indata.copy()
        if self._recording:
            self._frames.append(frame)
        else:
            self._pre_roll.append(frame)

    def start(self) -> None:
        if self._recording:
            logger.warning("Already recording, ignoring start()")
            return
        if not self._stream:
            # Try to re-open
            self._open_stream()
            if not self._stream:
                raise MicNotFoundError("No microphone available")

        self._frames = list(self._pre_roll)
        self._pre_roll.clear()
        self._stop_event.clear()
        self._recording = True

        self._max_timer = threading.Timer(self.max_seconds, self._auto_stop)
        self._max_timer.daemon = True
        self._max_timer.start()
        logger.info("Recording started (sr=%d, max=%ds, pre-roll=%d frames)",
                     self.sample_rate, self.max_seconds, len(self._frames))

    def _auto_stop(self) -> None:
        if self._recording:
            logger.info("Max duration reached, auto-stopping")
            self._do_stop()

    def stop(self) -> tuple[np.ndarray, str]:
        if not self._recording:
            raise RuntimeError("Not recording, cannot stop")
        return self._do_stop()

    def _do_stop(self) -> tuple[np.ndarray, str]:
        self._recording = False
        if self._max_timer:
            self._max_timer.cancel()
            self._max_timer = None

        if not self._frames:
            data = np.array([], dtype=np.int16)
        else:
            data = np.concatenate(self._frames, axis=0).flatten()

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
