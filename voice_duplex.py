#!/usr/bin/env python3
"""Full duplex voice conversation with interrupt detection.

Supports natural conversation where user can interrupt the AI while it's speaking.
"""

import asyncio
import numpy as np
import httpx
import io
import wave
import os
import sys
from pathlib import Path
from scipy import signal

# Load env
env_file = Path.home() / '.voicemode' / 'voicemode.env'
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            if k.strip() and v.strip() and k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

from livekit import rtc, api

API_KEY = os.getenv('LIVEKIT_API_KEY', 'devkey')
API_SECRET = os.getenv('LIVEKIT_API_SECRET', 'secret')
PARAKEET_KEY = os.getenv('PARAKEET_API_KEY', '')
LIVEKIT_URL = 'ws://127.0.0.1:7880'
ROOM_NAME = 'voicemode'
VOICE = 'am_adam'

# Interrupt detection threshold
SPEECH_THRESHOLD = 200  # Audio level to consider as speech
INTERRUPT_SAMPLES = 4800  # 100ms at 48kHz of speech to trigger interrupt


def generate_beep(freq=880, duration=0.15, sample_rate=48000):
    """Generate a beep tone."""
    t = np.linspace(0, duration, int(sample_rate * duration), False)
    envelope = np.ones_like(t)
    attack = int(0.01 * sample_rate)
    envelope[:attack] = np.linspace(0, 1, attack)
    envelope[-attack:] = np.linspace(1, 0, attack)
    tone = np.sin(2 * np.pi * freq * t) * envelope * 0.5
    return (tone * 32767).astype(np.int16)


class DuplexVoice:
    """Full duplex voice conversation handler."""

    def __init__(self):
        self.room = None
        self.audio_source = None
        self.audio_buffer = []
        self.is_speaking = False
        self.was_interrupted = False
        self.speech_detected_samples = 0

    async def connect(self):
        """Connect to LiveKit room."""
        token = api.AccessToken(API_KEY, API_SECRET)
        token.with_identity('claude-duplex')
        token.with_grants(api.VideoGrants(
            room_join=True,
            room=ROOM_NAME,
            can_publish=True,
            can_subscribe=True
        ))

        self.room = rtc.Room()

        @self.room.on('track_subscribed')
        def on_track(track, pub, part):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                asyncio.create_task(self._receive_audio(track))

        await self.room.connect(LIVEKIT_URL, token.to_jwt())

        # Set up TTS output
        self.audio_source = rtc.AudioSource(48000, 1)
        audio_track = rtc.LocalAudioTrack.create_audio_track('tts', self.audio_source)
        await self.room.local_participant.publish_track(audio_track)
        await asyncio.sleep(0.3)

        print('✅ Connected to LiveKit', file=sys.stderr)

    async def _receive_audio(self, track):
        """Receive audio and detect speech/interrupts."""
        stream = rtc.AudioStream(track)
        async for event in stream:
            samples = np.frombuffer(event.frame.data, dtype=np.int16)
            level = np.abs(samples).mean()

            # Always buffer for later transcription
            self.audio_buffer.append(samples)

            # Check for interrupt while speaking
            if self.is_speaking and level > SPEECH_THRESHOLD:
                self.speech_detected_samples += len(samples)
                if self.speech_detected_samples >= INTERRUPT_SAMPLES:
                    self.was_interrupted = True
                    print('⚡ INTERRUPT DETECTED', file=sys.stderr)
            else:
                self.speech_detected_samples = 0

    async def _send_audio(self, samples):
        """Send audio to remote, checking for interrupts."""
        chunk_size = 480  # 10ms at 48kHz
        for i in range(0, len(samples), chunk_size):
            if self.was_interrupted:
                break

            chunk = samples[i:i+chunk_size]
            if len(chunk) < chunk_size:
                chunk = np.pad(chunk, (0, chunk_size - len(chunk)))

            frame = rtc.AudioFrame.create(48000, 1, len(chunk))
            np.copyto(np.frombuffer(frame.data, dtype=np.int16), chunk)
            await self.audio_source.capture_frame(frame)

            # Small delay to allow interrupt detection
            if i % (chunk_size * 10) == 0:  # Every 100ms
                await asyncio.sleep(0.001)

    async def speak(self, text: str) -> bool:
        """Speak text via TTS. Returns True if completed, False if interrupted."""
        self.is_speaking = True
        self.was_interrupted = False
        self.speech_detected_samples = 0

        # Get TTS audio
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                'http://127.0.0.1:8880/v1/audio/speech',
                json={
                    'model': 'kokoro',
                    'input': text,
                    'voice': VOICE,
                    'response_format': 'pcm'
                }
            )

        if resp.status_code != 200:
            print(f'TTS error: {resp.status_code}', file=sys.stderr)
            self.is_speaking = False
            return True

        # Resample 24kHz -> 48kHz
        tts_audio = np.frombuffer(resp.content, dtype=np.int16)
        tts_48k = signal.resample(tts_audio, len(tts_audio) * 2).astype(np.int16)

        # Send with interrupt checking
        await self._send_audio(tts_48k)

        self.is_speaking = False
        return not self.was_interrupted

    async def beep(self):
        """Play a beep to signal listening."""
        beep_samples = generate_beep(880, 0.15)
        await self._send_audio(beep_samples)
        await asyncio.sleep(0.1)

    async def listen(self, duration: float = 10.0, min_speech: float = 0.5) -> str:
        """Listen for speech and transcribe."""
        self.audio_buffer.clear()

        # Wait for speech to start or timeout
        start = asyncio.get_event_loop().time()
        speech_start = None

        while True:
            await asyncio.sleep(0.05)
            now = asyncio.get_event_loop().time()

            if now - start > duration:
                break

            # Check if we have speech
            if self.audio_buffer:
                recent = self.audio_buffer[-1] if self.audio_buffer else np.array([])
                level = np.abs(recent).mean() if len(recent) > 0 else 0

                if level > SPEECH_THRESHOLD:
                    if speech_start is None:
                        speech_start = now
                else:
                    # Silence after speech?
                    if speech_start and (now - speech_start) > min_speech:
                        # Check for 1.5s of silence
                        if len(self.audio_buffer) >= 72:  # ~1.5s worth
                            last_chunks = self.audio_buffer[-72:]
                            last_audio = np.concatenate(last_chunks)
                            if np.abs(last_audio).mean() < SPEECH_THRESHOLD * 0.5:
                                print('🔇 Silence detected', file=sys.stderr)
                                break

        if not self.audio_buffer:
            return ""

        # Transcribe
        audio = np.concatenate(self.audio_buffer)
        if np.abs(audio).mean() < 50:
            return ""

        wav_buf = io.BytesIO()
        with wave.open(wav_buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(48000)
            wf.writeframes(audio.tobytes())
        wav_buf.seek(0)

        headers = {'Authorization': f'Bearer {PARAKEET_KEY}'} if PARAKEET_KEY else {}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                'http://127.0.0.1:2022/v1/audio/transcriptions',
                headers=headers,
                files={'file': ('audio.wav', wav_buf, 'audio/wav')},
                data={'model': 'parakeet-mlx'}
            )

        if resp.status_code == 200:
            return resp.json().get('text', '')
        return ""

    async def disconnect(self):
        """Disconnect from room."""
        if self.room:
            await self.room.disconnect()


async def main():
    """Demo conversation with interrupt support."""
    voice = DuplexVoice()
    await voice.connect()

    # Initial greeting
    print('🔊 Speaking...', file=sys.stderr)
    completed = await voice.speak(
        "I now have interrupt detection enabled. "
        "If you start speaking while I am talking, I will stop and listen to you. "
        "Try interrupting me during my next response to test it."
    )

    if not completed:
        print('(Interrupted!)', file=sys.stderr)

    await voice.beep()
    print('🎤 Listening...', file=sys.stderr)
    text = await voice.listen(10)

    if text:
        print(f'\n💬 YOU: {text}')

        # Respond with a longer message to allow interruption testing
        print('🔊 Speaking (try to interrupt)...', file=sys.stderr)
        completed = await voice.speak(
            "This is a longer response to give you time to interrupt me. "
            "I am speaking continuously so you can try talking over me. "
            "If you say something, I should detect it and stop talking. "
            "Go ahead and try it now if you would like to test the interrupt feature."
        )

        if not completed:
            print('⚡ (You interrupted!)', file=sys.stderr)
            # Listen to what they said
            await asyncio.sleep(0.5)
            text = await voice.listen(8)
            if text:
                print(f'💬 YOU: {text}')

    await voice.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
