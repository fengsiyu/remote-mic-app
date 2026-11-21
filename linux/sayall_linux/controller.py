"""Runtime coordination for the Linux ATVV voice and remote input paths."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Callable, Optional

from .atvv import (
    ATVVCapabilities,
    CAPABILITIES,
    MIC_OPEN,
    PCMFrameDecoder,
    STREAM_START,
    STREAM_STOP,
    SYNC,
    microphone_close,
    microphone_open,
    parse_capabilities,
    parse_stream_start,
    parse_sync,
    postprocess_pcm,
    voice_stop_reason,
)
from .audio import AudioPipeline
from .logging import RuntimeLogger


@dataclass
class ControllerState:
    bluetooth: str = "正在初始化"
    remote: str = "未连接"
    voice: str = "未就绪"
    audio: str = "未就绪"
    mapping: str = "未就绪"
    last_event: str = "等待操作"

    def updated(self, **changes: str) -> "ControllerState":
        return replace(self, **changes)


class VoiceController:
    def __init__(
        self,
        audio: AudioPipeline,
        logger: RuntimeLogger,
        on_state: Callable[[ControllerState], None],
        send_control: Callable[[bytes], bool],
        on_voice_activity: Optional[Callable[[bool], None]] = None,
    ) -> None:
        self.audio = audio
        self.logger = logger
        self.on_state = on_state
        self.send_control = send_control
        self.on_voice_activity = on_voice_activity
        self.state = ControllerState()
        self.capabilities: Optional[ATVVCapabilities] = None
        self.decoder = PCMFrameDecoder()
        self.streaming = False
        self.session_id = 0
        self.generation = 0
        self.session_started_at: Optional[float] = None
        self.voice_pressed_at: Optional[float] = None
        self.first_pcm_at: Optional[float] = None
        self.received_samples = 0
        self.scheduled_samples = 0
        self.last_stop_at: Optional[float] = None
        self._closed = False

    def update_state(self, **changes: str) -> None:
        self.state = self.state.updated(**changes)
        self.on_state(self.state)

    def set_bluetooth_connected(self, connected: bool, detail: str = "") -> None:
        if connected:
            self.update_state(bluetooth="已连接", remote=detail or "小米蓝牙遥控器")
            return
        if self.streaming:
            self.stop_stream(forced=True, reason="bluetooth_disconnected")
        self.update_state(bluetooth=detail or "未连接", remote="未连接", voice="未就绪")

    def set_mapping_state(self, text: str) -> None:
        self.update_state(mapping=text)

    def set_audio_state(self, text: str) -> None:
        self.update_state(audio=text)

    def set_remote_model(self, model: str) -> None:
        self.decoder.configure_model(model)
        self.logger.write(
            "ATVV MODEL phase=configure result=ok",
            {"family": "arn9" if "ARN9" in model.upper() else "rc"},
        )

    def note_voice_key_down(self, at: Optional[float] = None) -> None:
        self.voice_pressed_at = at if at is not None else time.monotonic()
        self.update_state(last_event="语音键按下")

    def note_voice_key_up(self) -> None:
        self.update_state(last_event="语音键松开")

    def handle_control(self, data: bytes, received_at: Optional[float] = None) -> None:
        if not data:
            return
        observed_at = received_at if received_at is not None else time.monotonic()
        opcode = data[0]
        if opcode == CAPABILITIES:
            capabilities = parse_capabilities(data)
            if capabilities is None:
                self.logger.write("ATVV CAPS phase=parse result=failed reason=invalid")
                return
            self.capabilities = capabilities
            self.logger.write(
                "ATVV CAPS phase=parsed result=ok",
                {
                    "version": "%04x" % capabilities.version,
                    "codec": capabilities.selected_codec,
                    "frame_size": capabilities.frame_size,
                    "sample_rate": capabilities.sample_rate,
                },
            )
            if not capabilities.supports_audio:
                self.update_state(voice="不支持 8 kHz 音频", last_event="语音链路不支持")
                return
            self.update_state(voice="语音链路就绪", last_event="已读取语音能力")
            return
        if opcode == MIC_OPEN:
            if self.capabilities is None:
                self.logger.write("ATVV MIC_OPEN phase=request result=rejected reason=caps_unknown")
                return
            if self.send_control(microphone_open(self.capabilities)):
                self.logger.write("ATVV MIC_OPEN phase=request result=written")
            else:
                self.logger.write("ATVV MIC_OPEN phase=request result=failed")
            return
        if opcode == STREAM_START:
            self._handle_stream_start(data, observed_at)
            return
        if opcode == STREAM_STOP:
            self.stop_stream(forced=False, reason=voice_stop_reason(data))
            return
        if opcode == SYNC:
            parsed = parse_sync(data)
            if parsed is not None and self.streaming:
                self.decoder.set_sync(parsed[0], parsed[1])
                self.logger.write("ATVV SYNC phase=accepted result=queued")

    def handle_audio(self, data: bytes, received_at: Optional[float] = None) -> None:
        if not data or self.capabilities is None:
            return
        if not self.streaming:
            if self.last_stop_at is not None and time.monotonic() - self.last_stop_at < 0.3:
                self.logger.write("ATVV AUDIO phase=receive result=ignored reason=after_stop")
                return
            self._begin_stream(self.session_id, implicit=True)
        samples = self.decoder.append_audio(data, self.capabilities.frame_size)
        if not samples:
            return
        processed = postprocess_pcm(samples, gain_db=0.0)
        submitted = self.audio.submit_samples(self.generation, processed)
        self.received_samples += len(processed)
        self.scheduled_samples += submitted
        now = time.monotonic()
        if self.first_pcm_at is None:
            self.first_pcm_at = now
            if self.voice_pressed_at is not None:
                latency_ms = round((now - self.voice_pressed_at) * 1000.0)
                latency_text = str(latency_ms)
            else:
                latency_text = "unknown"
            self.logger.write(
                "ATVV VOICE phase=first_pcm result=ok",
                {
                    "audio_generation": self.generation,
                    "trigger_down_to_first_pcm_ms": latency_text,
                    "samples": len(processed),
                },
            )
            self.update_state(last_event="正在收音")

    def _handle_stream_start(self, data: bytes, observed_at: float) -> None:
        parsed = parse_stream_start(data)
        if parsed is None or self.capabilities is None:
            return
        interaction, codec, session_id = parsed
        self.capabilities = self.capabilities.with_stream_codec(codec, interaction)
        if not self.capabilities.supports_audio:
            self.update_state(voice="不支持 8 kHz 音频", last_event="语音链路不支持")
            self.logger.write("ATVV STREAM phase=start result=rejected reason=unsupported_audio")
            return
        if self.streaming:
            self.logger.write("ATVV STREAM phase=start result=ignored reason=already_streaming")
            return
        self._begin_stream(session_id, implicit=False, observed_at=observed_at)

    def _begin_stream(self, session_id: int, implicit: bool, observed_at: Optional[float] = None) -> None:
        self.generation += 1
        self.session_id = session_id
        self.decoder.reset()
        self.streaming = True
        self.session_started_at = observed_at if observed_at is not None else time.monotonic()
        self.first_pcm_at = None
        self.received_samples = 0
        self.scheduled_samples = 0
        self.audio.begin_generation(self.generation)
        self.logger.write(
            "ATVV STREAM phase=start result=ok",
            {
                "audio_generation": self.generation,
                "session": session_id,
                "origin": "implicit_audio" if implicit else "remote_control",
            },
        )
        self.update_state(
            voice="正在收音" if implicit else "语音会话开始",
            last_event="正在收音" if implicit else "语音键按下",
        )
        if self.on_voice_activity is not None:
            self.on_voice_activity(True)

    def stop_stream(self, forced: bool, reason: str) -> None:
        if not self.streaming:
            return
        generation = self.generation
        pending = self.decoder.accumulator.pending_bytes
        received = self.received_samples
        scheduled = self.scheduled_samples
        started_at = self.session_started_at or time.monotonic()
        self.streaming = False
        self.last_stop_at = time.monotonic()
        self.decoder.accumulator.reset()
        completion_value = "forced" if forced else "normal"
        self.logger.write(
            "ATVV STREAM phase=stop result=accepted",
            {
                "audio_generation": generation,
                "completion": completion_value,
                "reason": reason,
                "partial_frame_bytes": pending,
            },
        )

        def completed(written_samples: int, pending_samples: int) -> None:
            duration_ms = max(0, round((time.monotonic() - started_at) * 1000.0))
            interrupted = pending_samples if forced else 0
            self.logger.write(
                "ATVV VOICE phase=completed result=ok",
                {
                    "audio_generation": generation,
                    "completion": completion_value,
                    "reason": reason,
                    "audio_received_samples": received,
                    "audio_scheduled_samples": scheduled,
                    "audio_played_samples": written_samples,
                    "audio_pending_samples": pending_samples,
                    "audio_interrupted_samples": interrupted,
                    "duration_ms": duration_ms,
                },
            )
            self.update_state(
                voice="语音链路就绪",
                last_event="语音会话完成" if not forced else "语音会话异常结束",
            )
            if self.on_voice_activity is not None:
                self.on_voice_activity(False)

        self.audio.finish_generation(generation, completed)

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.streaming:
            self.stop_stream(forced=True, reason="app_shutdown")
