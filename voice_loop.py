#!/usr/bin/env python3
"""Voice loop for Claude Code integration via LiveKit.

Captures speech from iPhone, transcribes it, and can speak responses back.
"""

import asyncio
import numpy as np
import httpx
import io
import wave
import os
import sys
import json
import subprocess
import argparse
from pathlib import Path
from datetime import datetime

# Load env
env_file = Path.home() / '.voicemode' / 'voicemode.env'
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            if k.strip() and v.strip() and k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

from livekit import rtc, api
from scipy import signal

API_KEY = os.getenv('LIVEKIT_API_KEY', 'devkey')
API_SECRET = os.getenv('LIVEKIT_API_SECRET', 'secret')
PARAKEET_KEY = os.getenv('PARAKEET_API_KEY', '')
LIVEKIT_URL = 'ws://127.0.0.1:7880'
ROOM_NAME = 'voicemode'

# Shared state
room = None
audio_source = None
audio_track = None


async def connect():
    """Connect to LiveKit room."""
    global room, audio_source, audio_track

    token = api.AccessToken(API_KEY, API_SECRET)
    token.with_identity('claude-code-voice')
    token.with_name('Claude Code')
    token.with_grants(api.VideoGrants(
        room_join=True,
        room=ROOM_NAME,
        can_publish=True,
        can_subscribe=True
    ))

    room = rtc.Room()
    await room.connect(LIVEKIT_URL, token.to_jwt())

    # Set up audio publishing
    audio_source = rtc.AudioSource(48000, 1)
    audio_track = rtc.LocalAudioTrack.create_audio_track('claude-tts', audio_source)
    await room.local_participant.publish_track(audio_track)

    return room


async def capture_speech(timeout=30, min_speech_ms=500, silence_ms=1500):
    """Capture speech with VAD-like behavior."""
    audio_buffer = []
    last_audio_time = None
    speech_started = False
    speech_start_time = None

    receiving_event = asyncio.Event()
    stop_event = asyncio.Event()

    async def receive_audio(track):
        nonlocal last_audio_time, speech_started, speech_start_time
        stream = rtc.AudioStream(track)
        async for event in stream:
            if stop_event.is_set():
                break
            samples = np.frombuffer(event.frame.data, dtype=np.int16)
            audio_buffer.append(samples)

            # Check audio level
            level = np.abs(samples).mean()
            now = asyncio.get_event_loop().time()

            if level > 100:  # Speech threshold
                last_audio_time = now
                if not speech_started:
                    speech_started = True
                    speech_start_time = now
                receiving_event.set()

    # Subscribe to remote audio tracks - ONLY from iphone-user
    for participant in room.remote_participants.values():
        if participant.identity == 'iphone-user':
            for pub in participant.track_publications.values():
                if pub.kind == rtc.TrackKind.KIND_AUDIO and pub.track:
                    print(f"  Subscribing to audio from {participant.identity}", file=sys.stderr)
                    asyncio.create_task(receive_audio(pub.track))

    @room.on('track_subscribed')
    def on_track(track, pub, part):
        # Only listen to iphone-user, ignore TTS and other participants
        if track.kind == rtc.TrackKind.KIND_AUDIO and part.identity == 'iphone-user':
            print(f"  Subscribing to audio from {part.identity}", file=sys.stderr)
            asyncio.create_task(receive_audio(track))

    # Wait for speech to start
    try:
        await asyncio.wait_for(receiving_event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return None, "timeout"

    # Continue capturing until silence
    start_time = asyncio.get_event_loop().time()
    while True:
        await asyncio.sleep(0.1)
        now = asyncio.get_event_loop().time()

        # Timeout check
        if now - start_time > timeout:
            break

        # Silence check (after minimum speech duration)
        if speech_started and last_audio_time:
            speech_duration = (now - speech_start_time) * 1000
            silence_duration = (now - last_audio_time) * 1000

            if speech_duration >= min_speech_ms and silence_duration >= silence_ms:
                break

    stop_event.set()

    if audio_buffer:
        return np.concatenate(audio_buffer), "ok"
    return None, "no_audio"


async def transcribe(audio, rate=48000):
    """Transcribe audio with Parakeet STT."""
    wav_buf = io.BytesIO()
    with wave.open(wav_buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
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
    return None


async def speak(text):
    """Speak text via TTS to iPhone."""
    global audio_source

    if not audio_source:
        print("Error: Not connected", file=sys.stderr)
        return

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            'http://127.0.0.1:8880/v1/audio/speech',
            json={
                'model': 'kokoro',
                'input': text,
                'voice': 'af_heart',
                'response_format': 'pcm'
            }
        )

    if resp.status_code != 200:
        print(f"TTS error: {resp.status_code}", file=sys.stderr)
        return

    # Convert 24kHz PCM to 48kHz for LiveKit
    tts_audio = np.frombuffer(resp.content, dtype=np.int16)
    tts_audio_48k = signal.resample(tts_audio, int(len(tts_audio) * 2)).astype(np.int16)

    # Send in 10ms chunks
    chunk_size = 480
    for i in range(0, len(tts_audio_48k), chunk_size):
        chunk = tts_audio_48k[i:i+chunk_size]
        if len(chunk) < chunk_size:
            chunk = np.pad(chunk, (0, chunk_size - len(chunk)))
        frame = rtc.AudioFrame.create(48000, 1, len(chunk))
        np.copyto(np.frombuffer(frame.data, dtype=np.int16), chunk)
        await audio_source.capture_frame(frame)


async def listen_once():
    """Listen for one utterance and return transcription."""
    audio, status = await capture_speech(timeout=30)

    if audio is None:
        return None

    text = await transcribe(audio)
    return text


def send_to_tmux(session: str, text: str) -> bool:
    """Send text to tmux session."""
    try:
        # Check if session exists
        result = subprocess.run(
            ["tmux", "has-session", "-t", session],
            capture_output=True
        )
        if result.returncode != 0:
            print(f"tmux session '{session}' not found", file=sys.stderr)
            return False

        # Send the text and Enter
        subprocess.run(
            ["tmux", "send-keys", "-t", session, text, "Enter"],
            check=True
        )
        return True
    except Exception as e:
        print(f"tmux error: {e}", file=sys.stderr)
        return False


PERMISSION_LOCK_FILE = "/tmp/voice-permission-active.lock"


async def voice_loop(tmux_session: str = None):
    """Main voice loop - listen and output transcriptions."""
    print("Voice loop started. Listening...", file=sys.stderr)
    if tmux_session:
        print(f"Sending transcriptions to tmux session: {tmux_session}", file=sys.stderr)

    while True:
        try:
            text = await listen_once()
            if text and text.strip():
                text = text.strip()

                # Output transcription as JSON for easy parsing
                output = {
                    "type": "transcription",
                    "text": text,
                    "timestamp": datetime.now().isoformat()
                }
                print(json.dumps(output), flush=True)

                # Check if permission hook is active - if so, don't send to tmux
                permission_active = os.path.exists(PERMISSION_LOCK_FILE)

                # Send to tmux if configured and permission hook not active
                if tmux_session and not permission_active:
                    if send_to_tmux(tmux_session, text):
                        print(f"  → Sent to tmux", file=sys.stderr)
                elif permission_active:
                    print(f"  → Skipped tmux (permission hook active)", file=sys.stderr)

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            await asyncio.sleep(1)


async def main(tmux_session: str = None):
    """Main entry point."""
    global room

    print("Connecting to LiveKit...", file=sys.stderr)
    await connect()
    print("Connected! Waiting for iPhone audio...", file=sys.stderr)

    # Check for participants
    if room.remote_participants:
        for p in room.remote_participants.values():
            print(f"  Found: {p.identity}", file=sys.stderr)
    else:
        print("  No participants yet - connect from iPhone", file=sys.stderr)

    try:
        await voice_loop(tmux_session)
    except KeyboardInterrupt:
        print("\nStopping...", file=sys.stderr)
    finally:
        if room:
            await room.disconnect()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Voice loop for LiveKit')
    parser.add_argument('--tmux', '-t', default=None,
                        help='tmux session to send transcriptions to')
    args = parser.parse_args()

    asyncio.run(main(args.tmux))
