"""Virtual microphone output and ordered ATVV audio draining."""

from __future__ import annotations

import os
import queue
import stat
import struct
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence, Tuple

from .atvv import pcm_to_bytes
from .logging import RuntimeLogger


@dataclass(frozen=True)
class AudioStatus:
    ready: bool
    source_name: str
    detail: str


class VirtualMic:
    SOURCE_NAME = "SayAll"

    def __init__(self, fifo_path: Path, logger: RuntimeLogger) -> None:
        self.fifo_path = fifo_path
        self.logger = logger
        self._module_id: Optional[str] = None
        self._write_fd: Optional[int] = None
        self._lock = threading.Lock()

    @property
    def write_fd(self) -> Optional[int]:
        with self._lock:
            return self._write_fd

    def start(self) -> AudioStatus:
        try:
            self.fifo_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.fifo_path.exists():
                os.mkfifo(str(self.fifo_path), 0o600)
            if not stat.S_ISFIFO(self.fifo_path.stat().st_mode):
                return AudioStatus(False, self.SOURCE_NAME, "fifo_invalid")
        except OSError as error:
            self.logger.write("AUDIO MIC phase=prepare result=failed reason=filesystem error=%s" % type(error).__name__)
            return AudioStatus(False, self.SOURCE_NAME, "filesystem")
        existing_module = self._find_existing_module_id()
        if existing_module is not None:
            self._unload_module(existing_module)
        if not self._source_exists():
            command = [
                "pactl",
                "load-module",
                "module-pipe-source",
                "source_name=%s" % self.SOURCE_NAME,
                "file=%s" % self.fifo_path,
                "format=s16le",
                "rate=16000",
                "channels=1",
            ]
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
            except (OSError, subprocess.SubprocessError) as error:
                self.logger.write("AUDIO MIC phase=load result=failed reason=pactl error=%s" % type(error).__name__)
                return AudioStatus(False, self.SOURCE_NAME, "pulse_unavailable")
            if result.returncode != 0 or not result.stdout.strip():
                self.logger.write("AUDIO MIC phase=load result=failed reason=module_load code=%s" % result.returncode)
                return AudioStatus(False, self.SOURCE_NAME, "module_load_failed")
            self._module_id = result.stdout.strip().splitlines()[-1]
        descriptor: Optional[int] = None
        last_error: Optional[OSError] = None
        for _ in range(20):
            try:
                descriptor = os.open(str(self.fifo_path), os.O_WRONLY | os.O_NONBLOCK)
                os.set_blocking(descriptor, True)
                break
            except OSError as error:
                last_error = error
                time.sleep(0.05)
        if descriptor is None:
            error_name = type(last_error).__name__ if last_error is not None else "OSError"
            self.logger.write("AUDIO MIC phase=open result=failed reason=fifo error=%s" % error_name)
            self._unload_owned_module()
            return AudioStatus(False, self.SOURCE_NAME, "fifo_open_failed")
        with self._lock:
            self._write_fd = descriptor
        self.logger.write("AUDIO MIC phase=ready result=ok source=SayAll")
        return AudioStatus(True, self.SOURCE_NAME, "ready")

    def _source_exists(self) -> bool:
        try:
            result = subprocess.run(
                ["pactl", "list", "short", "sources"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if result.returncode != 0:
            return False
        for line in result.stdout.splitlines():
            columns = line.split("\t")
            if len(columns) >= 2 and columns[1] == self.SOURCE_NAME:
                return True
        return False

    def _find_existing_module_id(self) -> Optional[str]:
        try:
            result = subprocess.run(
                ["pactl", "list", "short", "modules"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            columns = line.split("\t")
            if len(columns) < 3 or columns[1] != "module-pipe-source":
                continue
            if "source_name=%s" % self.SOURCE_NAME in columns[2]:
                return columns[0]
        return None

    def stop(self) -> None:
        with self._lock:
            descriptor = self._write_fd
            self._write_fd = None
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        self._unload_owned_module()
        try:
            if self.fifo_path.exists():
                self.fifo_path.unlink()
        except OSError:
            pass

    def _unload_owned_module(self) -> None:
        module_id = self._module_id
        self._module_id = None
        if not module_id:
            return
        self._unload_module(module_id)

    @staticmethod
    def _unload_module(module_id: str) -> None:
        try:
            subprocess.run(
                ["pactl", "unload-module", module_id],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass


class OrderedPCMWriter:
    SILENCE_CHUNK_SAMPLES = 320
    _STOP = object()

    def __init__(self, virtual_mic: VirtualMic, logger: RuntimeLogger) -> None:
        self.virtual_mic = virtual_mic
        self.logger = logger
        self._queue: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._pending_samples: Dict[int, int] = {}
        self._written_samples: Dict[int, int] = {}
        self._finished: set = set()
        self._completion_callbacks: Dict[int, Callable[[int, int], None]] = {}
        self._started_at: Optional[float] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="sayall-pcm-writer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._queue.put((-1, self._STOP))
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2)

    def begin_generation(self, generation: int) -> None:
        with self._lock:
            self._pending_samples.setdefault(generation, 0)
            self._written_samples.setdefault(generation, 0)
            self._finished.discard(generation)
            self._started_at = self._started_at or time.monotonic()

    def enqueue_pcm(self, generation: int, pcm: bytes, completion: Optional[Callable[[int, int], None]] = None) -> None:
        if not pcm:
            return
        sample_count = len(pcm) // 2
        with self._lock:
            self._pending_samples[generation] = self._pending_samples.get(generation, 0) + sample_count
            if completion is not None:
                self._completion_callbacks[generation] = completion
        self._queue.put((generation, pcm))

    def finish_generation(self, generation: int, completion: Callable[[int, int], None]) -> None:
        with self._lock:
            self._completion_callbacks[generation] = completion
        self._queue.put((generation, self._STOP))

    def pending_samples(self, generation: int) -> int:
        with self._lock:
            return self._pending_samples.get(generation, 0)

    def written_samples(self, generation: int) -> int:
        with self._lock:
            return self._written_samples.get(generation, 0)

    def _run(self) -> None:
        next_silence = time.monotonic()
        while not self._stop_event.is_set():
            try:
                generation, payload = self._queue.get(timeout=0.01)
            except queue.Empty:
                descriptor = self.virtual_mic.write_fd
                now = time.monotonic()
                if descriptor is not None and now >= next_silence:
                    silence = b"\x00\x00" * self.SILENCE_CHUNK_SAMPLES
                    if not self._write_all(descriptor, silence):
                        time.sleep(0.02)
                    next_silence = now + self.SILENCE_CHUNK_SAMPLES / 16000.0
                continue
            if payload is self._STOP:
                if generation == -1:
                    break
                self._complete(generation)
                continue
            descriptor = self.virtual_mic.write_fd
            if descriptor is None:
                with self._lock:
                    self._pending_samples[generation] = max(
                        0, self._pending_samples.get(generation, 0) - len(payload) // 2
                    )
                continue
            if not self._write_all(descriptor, payload):
                time.sleep(0.01)
                continue
            sample_count = len(payload) // 2
            with self._lock:
                self._pending_samples[generation] = max(
                    0, self._pending_samples.get(generation, 0) - sample_count
                )
                self._written_samples[generation] = self._written_samples.get(generation, 0) + sample_count

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> bool:
        view = memoryview(payload)
        while view:
            try:
                written = os.write(descriptor, view)
            except BlockingIOError:
                time.sleep(0.002)
                continue
            except OSError:
                return False
            if written <= 0:
                return False
            view = view[written:]
        return True

    def _complete(self, generation: int) -> None:
        with self._lock:
            pending = self._pending_samples.get(generation, 0)
            written = self._written_samples.get(generation, 0)
            callback = self._completion_callbacks.pop(generation, None)
            self._finished.add(generation)
        if callback is not None:
            callback(written, pending)


class AudioPipeline:
    def __init__(
        self,
        virtual_mic: VirtualMic,
        writer: OrderedPCMWriter,
        logger: RuntimeLogger,
        volume_getter: Callable[[], int],
    ) -> None:
        self.virtual_mic = virtual_mic
        self.writer = writer
        self.logger = logger
        self.volume_getter = volume_getter
        self._status = AudioStatus(False, VirtualMic.SOURCE_NAME, "not_started")

    @property
    def status(self) -> AudioStatus:
        return self._status

    def start(self) -> AudioStatus:
        self._status = self.virtual_mic.start()
        if self._status.ready:
            self.writer.start()
        return self._status

    def stop(self) -> None:
        self.writer.stop()
        self.virtual_mic.stop()
        self._status = AudioStatus(False, VirtualMic.SOURCE_NAME, "stopped")

    def begin_generation(self, generation: int) -> None:
        self.writer.begin_generation(generation)

    def submit_samples(self, generation: int, samples: Sequence[int]) -> int:
        if not samples:
            return 0
        volume = min(10.0, max(0.0, self.volume_getter() / 100.0))
        scaled = [min(32767, max(-32768, int(round(sample * volume)))) for sample in samples]
        self.writer.enqueue_pcm(generation, pcm_to_bytes(scaled))
        return len(scaled)

    def finish_generation(self, generation: int, completion: Callable[[int, int], None]) -> None:
        self.writer.finish_generation(generation, completion)
