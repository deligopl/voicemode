"""Audio transport abstraction for VoiceMode.

This module provides a pluggable audio transport layer that can use either:
- Local sounddevice (default) - for direct microphone/speaker access
- LiveKit (remote) - for audio over WebRTC from remote clients (e.g., iPhone)

Configuration:
    VOICEMODE_AUDIO_TRANSPORT=local (default) or livekit
    VOICEMODE_LIVEKIT_URL=wss://your-livekit-server:7880
    VOICEMODE_LIVEKIT_API_KEY=your-api-key
    VOICEMODE_LIVEKIT_API_SECRET=your-api-secret
    VOICEMODE_LIVEKIT_ROOM=voicemode (default room name)
"""

import asyncio
import logging
import os
import queue
import threading
import time
from abc import ABC, abstractmethod
from typing import Optional, Tuple, Callable
from dataclasses import dataclass

import numpy as np

from .config import (
    AUDIO_TRANSPORT as CONFIG_AUDIO_TRANSPORT,
    LIVEKIT_URL as CONFIG_LIVEKIT_URL,
    LIVEKIT_API_KEY as CONFIG_LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET as CONFIG_LIVEKIT_API_SECRET,
    LIVEKIT_ROOM as CONFIG_LIVEKIT_ROOM,
)

logger = logging.getLogger("voicemode.audio_transport")


@dataclass
class AudioConfig:
    """Audio configuration for transport."""
    sample_rate: int = 24000
    channels: int = 1
    dtype: type = np.int16


class AudioTransport(ABC):
    """Abstract base class for audio transport."""

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the audio transport."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the audio transport."""
        pass

    @abstractmethod
    async def record(self, duration: float, config: AudioConfig) -> np.ndarray:
        """Record audio for specified duration.

        Args:
            duration: Recording duration in seconds
            config: Audio configuration

        Returns:
            Recorded audio samples as numpy array
        """
        pass

    @abstractmethod
    async def record_with_vad(
        self,
        max_duration: float,
        min_duration: float,
        config: AudioConfig,
        vad_callback: Optional[Callable[[bytes], bool]] = None,
        silence_threshold_ms: int = 1000
    ) -> Tuple[np.ndarray, bool]:
        """Record audio with voice activity detection.

        Args:
            max_duration: Maximum recording duration in seconds
            min_duration: Minimum recording duration before VAD activates
            config: Audio configuration
            vad_callback: Optional callback for VAD (returns True if speech detected)
            silence_threshold_ms: Silence duration to stop recording

        Returns:
            Tuple of (audio samples, speech_detected)
        """
        pass

    @abstractmethod
    async def play(self, samples: np.ndarray, config: AudioConfig) -> None:
        """Play audio samples.

        Args:
            samples: Audio samples to play
            config: Audio configuration
        """
        pass

    @abstractmethod
    async def play_streaming(
        self,
        audio_generator,
        config: AudioConfig,
        on_chunk: Optional[Callable[[bytes], None]] = None
    ) -> None:
        """Play audio from a streaming generator.

        Args:
            audio_generator: Async generator yielding audio chunks
            config: Audio configuration
            on_chunk: Optional callback for each chunk
        """
        pass

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if transport is connected."""
        pass

    @property
    @abstractmethod
    def transport_type(self) -> str:
        """Get transport type name."""
        pass


class LocalAudioTransport(AudioTransport):
    """Local audio transport using sounddevice."""

    def __init__(self):
        self._connected = False

    async def connect(self) -> None:
        """Initialize sounddevice."""
        import sounddevice as sd
        # Refresh device list
        sd._terminate()
        sd._initialize()
        self._connected = True
        logger.info("Local audio transport connected")

    async def disconnect(self) -> None:
        """Cleanup sounddevice."""
        self._connected = False
        logger.info("Local audio transport disconnected")

    async def record(self, duration: float, config: AudioConfig) -> np.ndarray:
        """Record audio using sounddevice."""
        import sounddevice as sd

        samples_to_record = int(duration * config.sample_rate)
        recording = sd.rec(
            samples_to_record,
            samplerate=config.sample_rate,
            channels=config.channels,
            dtype=config.dtype
        )
        sd.wait()
        return recording.flatten()

    async def record_with_vad(
        self,
        max_duration: float,
        min_duration: float,
        config: AudioConfig,
        vad_callback: Optional[Callable[[bytes], bool]] = None,
        silence_threshold_ms: int = 1000
    ) -> Tuple[np.ndarray, bool]:
        """Record with VAD using sounddevice InputStream."""
        import sounddevice as sd

        audio_chunks = []
        speech_detected = False
        silence_start = None
        recording_start = time.time()

        # VAD chunk size (30ms)
        chunk_duration = 0.03
        chunk_samples = int(config.sample_rate * chunk_duration)

        audio_queue = queue.Queue()

        def audio_callback(indata, frames, time_info, status):
            if status:
                logger.warning(f"Audio callback status: {status}")
            audio_queue.put(indata.copy())

        with sd.InputStream(
            samplerate=config.sample_rate,
            channels=config.channels,
            dtype=config.dtype,
            callback=audio_callback,
            blocksize=chunk_samples
        ):
            while True:
                elapsed = time.time() - recording_start

                if elapsed >= max_duration:
                    break

                try:
                    chunk = audio_queue.get(timeout=0.1)
                    audio_chunks.append(chunk)

                    # VAD check after minimum duration
                    if elapsed >= min_duration and vad_callback:
                        chunk_bytes = chunk.tobytes()
                        is_speech = vad_callback(chunk_bytes)

                        if is_speech:
                            speech_detected = True
                            silence_start = None
                        else:
                            if silence_start is None:
                                silence_start = time.time()
                            elif (time.time() - silence_start) * 1000 >= silence_threshold_ms:
                                logger.info("Silence detected, stopping recording")
                                break
                    else:
                        speech_detected = True  # Assume speech during grace period

                except queue.Empty:
                    continue

        if audio_chunks:
            return np.concatenate(audio_chunks).flatten(), speech_detected
        return np.array([], dtype=config.dtype), False

    async def play(self, samples: np.ndarray, config: AudioConfig) -> None:
        """Play audio using sounddevice."""
        import sounddevice as sd

        # Ensure float32 for playback
        if samples.dtype != np.float32:
            samples = samples.astype(np.float32)
            if samples.max() > 1.0:
                samples = samples / 32768.0

        sd.play(samples, samplerate=config.sample_rate)
        sd.wait()

    async def play_streaming(
        self,
        audio_generator,
        config: AudioConfig,
        on_chunk: Optional[Callable[[bytes], None]] = None
    ) -> None:
        """Play streaming audio using NonBlockingAudioPlayer."""
        from .audio_player import NonBlockingAudioPlayer

        player = NonBlockingAudioPlayer()
        all_samples = []

        async for chunk in audio_generator:
            if on_chunk:
                on_chunk(chunk)
            # Decode and accumulate
            samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            all_samples.append(samples)

        if all_samples:
            combined = np.concatenate(all_samples)
            player.play(combined, sample_rate=config.sample_rate, blocking=True)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def transport_type(self) -> str:
        return "local"


class LiveKitAudioTransport(AudioTransport):
    """LiveKit audio transport for remote clients."""

    def __init__(
        self,
        url: str = None,
        api_key: str = None,
        api_secret: str = None,
        room_name: str = None
    ):
        # Use config values as defaults
        url = url or CONFIG_LIVEKIT_URL
        api_key = api_key or CONFIG_LIVEKIT_API_KEY
        api_secret = api_secret or CONFIG_LIVEKIT_API_SECRET
        room_name = room_name or CONFIG_LIVEKIT_ROOM
        self.url = url
        self.api_key = api_key
        self.api_secret = api_secret
        self.room_name = room_name
        self.room = None
        self.audio_source = None
        self.audio_track = None
        self._connected = False
        self._remote_audio_queue = asyncio.Queue()
        self._participant_identity = "voicemode-server"

    async def connect(self) -> None:
        """Connect to LiveKit room."""
        try:
            from livekit import rtc, api
        except ImportError:
            raise ImportError(
                "LiveKit SDK not installed. Install with: pip install livekit"
            )

        # Generate access token
        token = api.AccessToken(self.api_key, self.api_secret)
        token.with_identity(self._participant_identity)
        token.with_name("VoiceMode Server")
        token.with_grants(api.VideoGrants(
            room_join=True,
            room=self.room_name,
            can_publish=True,
            can_subscribe=True
        ))
        jwt_token = token.to_jwt()

        # Create and connect to room
        self.room = rtc.Room()

        # Set up event handlers
        @self.room.on("track_subscribed")
        def on_track_subscribed(track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                logger.info(f"Subscribed to audio from {participant.identity}")
                asyncio.create_task(self._handle_audio_track(track))

        @self.room.on("participant_connected")
        def on_participant_connected(participant):
            logger.info(f"Participant connected: {participant.identity}")

        @self.room.on("participant_disconnected")
        def on_participant_disconnected(participant):
            logger.info(f"Participant disconnected: {participant.identity}")

        await self.room.connect(self.url, jwt_token)

        # Create audio source for publishing
        self.audio_source = rtc.AudioSource(24000, 1)
        self.audio_track = rtc.LocalAudioTrack.create_audio_track(
            "voicemode-tts",
            self.audio_source
        )

        # Publish our audio track
        await self.room.local_participant.publish_track(self.audio_track)

        self._connected = True
        logger.info(f"Connected to LiveKit room: {self.room_name}")

    async def _handle_audio_track(self, track) -> None:
        """Handle incoming audio from remote participant."""
        from livekit import rtc

        audio_stream = rtc.AudioStream(track)

        async for event in audio_stream:
            # Convert frame to numpy array (event.frame contains the actual AudioFrame)
            samples = np.frombuffer(event.frame.data, dtype=np.int16)
            await self._remote_audio_queue.put(samples)

    async def disconnect(self) -> None:
        """Disconnect from LiveKit room."""
        if self.room:
            await self.room.disconnect()
            self.room = None
        self.audio_source = None
        self.audio_track = None
        self._connected = False
        logger.info("Disconnected from LiveKit room")

    async def record(self, duration: float, config: AudioConfig) -> np.ndarray:
        """Record audio from remote participant."""
        if not self._connected:
            raise RuntimeError("Not connected to LiveKit room")

        samples_needed = int(duration * config.sample_rate)
        collected_samples = []
        collected_count = 0

        start_time = time.time()

        while collected_count < samples_needed:
            try:
                # Timeout based on remaining time
                remaining = duration - (time.time() - start_time)
                if remaining <= 0:
                    break

                samples = await asyncio.wait_for(
                    self._remote_audio_queue.get(),
                    timeout=min(remaining, 0.5)
                )
                collected_samples.append(samples)
                collected_count += len(samples)

            except asyncio.TimeoutError:
                continue

        if collected_samples:
            return np.concatenate(collected_samples)[:samples_needed]
        return np.array([], dtype=config.dtype)

    async def record_with_vad(
        self,
        max_duration: float,
        min_duration: float,
        config: AudioConfig,
        vad_callback: Optional[Callable[[bytes], bool]] = None,
        silence_threshold_ms: int = 1000
    ) -> Tuple[np.ndarray, bool]:
        """Record from remote with VAD."""
        if not self._connected:
            raise RuntimeError("Not connected to LiveKit room")

        audio_chunks = []
        speech_detected = False
        silence_start = None
        recording_start = time.time()

        while True:
            elapsed = time.time() - recording_start

            if elapsed >= max_duration:
                break

            try:
                samples = await asyncio.wait_for(
                    self._remote_audio_queue.get(),
                    timeout=0.1
                )
                audio_chunks.append(samples)

                # VAD check after minimum duration
                if elapsed >= min_duration and vad_callback:
                    chunk_bytes = samples.tobytes()
                    is_speech = vad_callback(chunk_bytes)

                    if is_speech:
                        speech_detected = True
                        silence_start = None
                    else:
                        if silence_start is None:
                            silence_start = time.time()
                        elif (time.time() - silence_start) * 1000 >= silence_threshold_ms:
                            logger.info("Silence detected, stopping recording")
                            break
                else:
                    speech_detected = True

            except asyncio.TimeoutError:
                # Check for extended silence
                if silence_start and (time.time() - silence_start) * 1000 >= silence_threshold_ms:
                    break
                continue

        if audio_chunks:
            return np.concatenate(audio_chunks), speech_detected
        return np.array([], dtype=config.dtype), False

    async def play(self, samples: np.ndarray, config: AudioConfig) -> None:
        """Send audio to remote participant via LiveKit."""
        if not self._connected or not self.audio_source:
            raise RuntimeError("Not connected to LiveKit room")

        from livekit import rtc

        # Ensure int16 format
        if samples.dtype != np.int16:
            if samples.dtype == np.float32:
                samples = (samples * 32768).astype(np.int16)
            else:
                samples = samples.astype(np.int16)

        # Create audio frame
        frame = rtc.AudioFrame.create(
            config.sample_rate,
            config.channels,
            len(samples)
        )
        np.copyto(np.frombuffer(frame.data, dtype=np.int16), samples)

        # Capture (send) the frame
        await self.audio_source.capture_frame(frame)

    async def play_streaming(
        self,
        audio_generator,
        config: AudioConfig,
        on_chunk: Optional[Callable[[bytes], None]] = None
    ) -> None:
        """Stream audio to remote participant."""
        if not self._connected or not self.audio_source:
            raise RuntimeError("Not connected to LiveKit room")

        from livekit import rtc

        async for chunk in audio_generator:
            if on_chunk:
                on_chunk(chunk)

            # Decode chunk to samples
            samples = np.frombuffer(chunk, dtype=np.int16)

            # Create and send frame
            frame = rtc.AudioFrame.create(
                config.sample_rate,
                config.channels,
                len(samples)
            )
            np.copyto(np.frombuffer(frame.data, dtype=np.int16), samples)
            await self.audio_source.capture_frame(frame)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def transport_type(self) -> str:
        return "livekit"


# Global transport instance
_transport: Optional[AudioTransport] = None


def get_audio_transport() -> AudioTransport:
    """Get the configured audio transport instance."""
    global _transport

    if _transport is None:
        if CONFIG_AUDIO_TRANSPORT == "livekit":
            _transport = LiveKitAudioTransport()
            logger.info("Using LiveKit audio transport")
        else:
            _transport = LocalAudioTransport()
            logger.info("Using local audio transport")

    return _transport


async def ensure_connected() -> AudioTransport:
    """Ensure transport is connected and return it."""
    transport = get_audio_transport()
    if not transport.is_connected:
        await transport.connect()
    return transport
