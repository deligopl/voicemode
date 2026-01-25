#!/usr/bin/env python3
"""Test LiveKit audio → Parakeet STT pipeline."""

import asyncio
import tempfile
import time
import wave
import numpy as np
import httpx
from livekit import rtc, api

import os

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://127.0.0.1:7880")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secret")
ROOM_NAME = os.getenv("VOICEMODE_LIVEKIT_ROOM", "voicemode")
STT_URL = os.getenv("VOICEMODE_STT_URL", "http://127.0.0.1:2022/v1/audio/transcriptions")
PARAKEET_API_KEY = os.getenv("PARAKEET_API_KEY")
SAMPLE_RATE = 48000  # LiveKit default


async def main():
    print("Connecting to LiveKit...")

    # Create token
    token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
        .with_identity("stt-test") \
        .with_name("STT Test") \
        .with_grants(api.VideoGrants(
            room_join=True,
            room=ROOM_NAME,
            can_subscribe=True,
        ))

    # Connect to room
    room = rtc.Room()
    await room.connect(LIVEKIT_URL, token.to_jwt())
    print(f"Connected to room: {ROOM_NAME}")

    # Find remote participant with audio
    audio_samples = []
    recording = False
    record_start = None
    record_duration = 5.0  # Record for 5 seconds

    async def record_from_track(track, participant):
        nonlocal recording, record_start, audio_samples

        print(f"Subscribed to audio from: {participant.identity}")

        audio_stream = rtc.AudioStream(track)
        recording = True
        record_start = time.time()

        async for event in audio_stream:
            if not recording:
                break

            # Get audio data
            frame = event.frame
            samples = np.frombuffer(frame.data, dtype=np.int16)
            audio_samples.append(samples)

            # Check duration
            elapsed = time.time() - record_start
            if elapsed >= record_duration:
                print(f"Recorded {elapsed:.1f}s of audio")
                recording = False
                break

    def on_track_subscribed(track, publication, participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.create_task(record_from_track(track, participant))

    room.on("track_subscribed", on_track_subscribed)

    # Wait for participants
    print("Waiting for audio from iPhone...")
    print("(Speak into your iPhone now!)")

    # Check existing participants
    for participant in room.remote_participants.values():
        for publication in participant.track_publications.values():
            if publication.track and publication.kind == rtc.TrackKind.KIND_AUDIO:
                asyncio.create_task(record_from_track(publication.track, participant))

    # Wait for recording to complete
    timeout = 30
    start = time.time()
    while recording or (not audio_samples and time.time() - start < timeout):
        await asyncio.sleep(0.1)
        if not recording and audio_samples:
            break

    await room.disconnect()

    if not audio_samples:
        print("No audio received!")
        return

    # Combine audio samples
    print(f"Processing {len(audio_samples)} audio chunks...")
    audio_data = np.concatenate(audio_samples)
    print(f"Total samples: {len(audio_data)}, duration: {len(audio_data)/SAMPLE_RATE:.1f}s")

    # Save to temp WAV file
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        with wave.open(tmp.name, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)  # 16-bit
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(audio_data.tobytes())
        tmp_path = tmp.name

    print(f"Saved audio to: {tmp_path}")

    # Send to Parakeet STT
    print("Sending to Parakeet STT...")
    start = time.time()

    # Build headers with API key if configured
    headers = {}
    if PARAKEET_API_KEY:
        headers["Authorization"] = f"Bearer {PARAKEET_API_KEY}"

    async with httpx.AsyncClient() as client:
        with open(tmp_path, 'rb') as f:
            response = await client.post(
                STT_URL,
                files={"file": ("audio.wav", f, "audio/wav")},
                data={"model": "parakeet-tdt-0.6b-v3"},
                headers=headers,
                timeout=60.0
            )

    elapsed = time.time() - start
    print(f"STT took: {elapsed:.2f}s")

    if response.status_code == 200:
        result = response.json()
        print(f"\n=== Transcription ===")
        print(result.get("text", result))
    else:
        print(f"Error: {response.status_code}")
        print(response.text)


if __name__ == "__main__":
    asyncio.run(main())
