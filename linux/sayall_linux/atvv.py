"""ATVV protocol helpers shared by the Linux Bluetooth and audio paths."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple


SERVICE_UUID = "ab5e0001-5a21-4f05-bc7d-af01f617b664"
TRANSMIT_UUID = "ab5e0002-5a21-4f05-bc7d-af01f617b664"
AUDIO_UUID = "ab5e0003-5a21-4f05-bc7d-af01f617b664"
CONTROL_UUID = "ab5e0004-5a21-4f05-bc7d-af01f617b664"
DEVICE_INFORMATION_UUID = "0000180a-0000-1000-8000-00805f9b34fb"
MODEL_NUMBER_UUID = "00002a24-0000-1000-8000-00805f9b34fb"
BATTERY_SERVICE_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

GET_CAPABILITIES_V10 = bytes([0x0A, 0x01, 0x00, 0x00, 0x03, 0x03])
MIC_OPEN = 0x08
MIC_CLOSE = 0x0D
GET_CAPABILITIES = 0x0A
CAPABILITIES = 0x0B
STREAM_START = 0x04
STREAM_STOP = 0x00
SYNC = 0x0A

SAMPLE_RATE_16K = 16000
REAL_TIME_SAMPLES_PER_MILLISECOND = 16.0


@dataclass(frozen=True)
class ATVVCapabilities:
    version: int
    codecs: int
    interaction: int
    frame_size: int
    selected_codec: int
    sample_rate: int

    @property
    def supports_audio(self) -> bool:
        return self.sample_rate == SAMPLE_RATE_16K

    @property
    def frame_samples(self) -> int:
        return self.frame_size * 2

    def with_stream_codec(self, codec: int, interaction: int) -> "ATVVCapabilities":
        return ATVVCapabilities(
            version=self.version,
            codecs=self.codecs,
            interaction=interaction,
            frame_size=self.frame_size,
            selected_codec=codec,
            sample_rate=SAMPLE_RATE_16K if codec == 0x02 else 8000,
        )


def parse_capabilities(data: bytes) -> Optional[ATVVCapabilities]:
    if len(data) < 7 or data[0] != CAPABILITIES:
        return None
    version = (data[1] << 8) | data[2]
    if version >= 0x0100:
        codecs = data[3]
        interaction = data[4]
        if codecs == 0 and len(data) >= 9 and data[4] & 0x03:
            codecs = data[4]
            interaction = 0x03
    else:
        if len(data) < 9:
            return None
        codecs = data[4]
        interaction = 0
    frame_size = (data[5] << 8) | data[6]
    selected_codec = 0x02 if codecs & 0x02 else 0x01
    return ATVVCapabilities(
        version=version,
        codecs=codecs,
        interaction=interaction,
        frame_size=frame_size or 120,
        selected_codec=selected_codec,
        sample_rate=SAMPLE_RATE_16K if selected_codec == 0x02 else 8000,
    )


def microphone_open(capabilities: ATVVCapabilities) -> bytes:
    if capabilities.version >= 0x0100:
        return bytes([0x0C, 0x00])
    return bytes([0x0C, 0x00, capabilities.selected_codec])


def microphone_close(capabilities: ATVVCapabilities, session_id: int) -> bytes:
    if capabilities.version >= 0x0100:
        return bytes([MIC_CLOSE, session_id & 0xFF])
    return bytes([MIC_CLOSE])


def microphone_extend(capabilities: ATVVCapabilities, session_id: int) -> Optional[bytes]:
    if capabilities.version < 0x0100:
        return None
    return bytes([0x0E, session_id & 0xFF])


def parse_stream_start(data: bytes) -> Optional[Tuple[int, int, int]]:
    if len(data) < 2 or data[0] != STREAM_START:
        return None
    interaction = data[1]
    codec = data[2] if len(data) >= 3 else 0x01
    session_id = data[3] if len(data) >= 4 else 0
    return interaction, codec, session_id


def parse_sync(data: bytes) -> Optional[Tuple[int, int]]:
    if len(data) < 7 or data[0] != SYNC:
        return None
    predictor = struct.unpack(">h", bytes(data[4:6]))[0]
    return predictor, data[6]


def voice_stop_reason(data: bytes) -> str:
    if len(data) >= 2 and data[1] == 0x08:
        return "remote_timeout"
    return "remote_release"


def expected_frame_count(duration_milliseconds: int, frame_samples: int) -> int:
    if duration_milliseconds <= 0 or frame_samples <= 0:
        return 0
    milliseconds_per_frame = frame_samples / REAL_TIME_SAMPLES_PER_MILLISECOND
    return int(round(duration_milliseconds / milliseconds_per_frame))


def audio_coverage_percent(received_samples: int, duration_milliseconds: int) -> float:
    if duration_milliseconds <= 0:
        return 0.0
    return received_samples / REAL_TIME_SAMPLES_PER_MILLISECOND / duration_milliseconds * 100.0


class IMAADPCMDecoder:
    STEP_TABLE = (
        7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
        34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130,
        143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449,
        494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411,
        1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026,
        4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
        11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
        27086, 29794, 32767,
    )
    INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8)

    def __init__(self) -> None:
        self.predictor = 0
        self.step_index = 0
        self.low_nibble_first = False

    def reset(self, predictor: int = 0, step_index: int = 0) -> None:
        self.predictor = min(32767, max(-32768, predictor))
        self.step_index = min(88, max(0, step_index))

    def decode(self, data: bytes) -> List[int]:
        samples: List[int] = []
        samples.extend([0] * (len(data) * 2))
        output_index = 0
        for byte in data:
            if self.low_nibble_first:
                first, second = byte & 0x0F, byte >> 4
            else:
                first, second = byte >> 4, byte & 0x0F
            samples[output_index] = self._decode_nibble(first)
            samples[output_index + 1] = self._decode_nibble(second)
            output_index += 2
        return samples

    def _decode_nibble(self, nibble: int) -> int:
        step = self.STEP_TABLE[self.step_index]
        difference = step >> 3
        if nibble & 1:
            difference += step >> 2
        if nibble & 2:
            difference += step >> 1
        if nibble & 4:
            difference += step
        self.predictor += -difference if nibble & 8 else difference
        self.predictor = min(32767, max(-32768, self.predictor))
        self.step_index = min(88, max(0, self.step_index + self.INDEX_TABLE[nibble & 7]))
        return self.predictor


def postprocess_pcm(samples: Sequence[int], gain_db: float = 0.0) -> List[int]:
    if not samples:
        return []
    filtered = list(samples)
    if len(samples) >= 3:
        for index in range(1, len(samples) - 1):
            filtered[index] = (samples[index - 1] + 2 * samples[index] + samples[index + 1]) >> 2
    safe_gain_db = min(24.0, max(-24.0, gain_db if math.isfinite(gain_db) else 0.0))
    gain = math.pow(10.0, safe_gain_db / 20.0)
    return [min(32767, max(-32768, int(round(value * gain)))) for value in filtered]


def pcm_to_bytes(samples: Sequence[int]) -> bytes:
    if not samples:
        return b""
    clamped = [min(32767, max(-32768, int(value))) for value in samples]
    return struct.pack("<%dh" % len(clamped), *clamped)


class FrameAccumulator:
    def __init__(self) -> None:
        self._pending = bytearray()

    @property
    def pending_bytes(self) -> int:
        return len(self._pending)

    def append(self, data: bytes, frame_size: int) -> List[bytes]:
        if frame_size <= 0:
            return []
        self._pending.extend(data)
        frames: List[bytes] = []
        while len(self._pending) >= frame_size:
            frames.append(bytes(self._pending[:frame_size]))
            del self._pending[:frame_size]
        return frames

    def reset(self) -> None:
        self._pending.clear()


class PCMFrameDecoder:
    def __init__(self) -> None:
        self.accumulator = FrameAccumulator()
        self.decoder = IMAADPCMDecoder()
        self._pending_sync: Optional[Tuple[int, int]] = None

    def reset(self) -> None:
        self.accumulator.reset()
        self.decoder.reset()
        self._pending_sync = None

    def set_sync(self, predictor: int, step_index: int) -> None:
        self._pending_sync = (predictor, step_index)

    def configure_model(self, model_number: str) -> None:
        self.decoder.low_nibble_first = "ARN9" in model_number.upper()

    def append_audio(self, data: bytes, frame_size: int) -> List[int]:
        decoded: List[int] = []
        for frame in self.accumulator.append(data, frame_size):
            if self._pending_sync is not None:
                predictor, step_index = self._pending_sync
                self.decoder.reset(predictor=predictor, step_index=step_index)
                self._pending_sync = None
            decoded.extend(self.decoder.decode(frame))
        return decoded
