#!/usr/bin/env python3
"""Test full voice pipeline: iPhone → STT → TTS → iPhone."""

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
TTS_URL = os.getenv("VOICEMODE_TTS_URL", "http://127.0.0.1:8880/v1/audio/speech")
PARAKEET_API_KEY = os.getenv("PARAKEET_API_KEY")
SAMPLE_RATE = 48000


async def main():
    print("=" * 50)
    print("Full Voice Pipeline Test")
    print("=" * 50)
    print("\nConnecting to LiveKit...")

    # Create token with publish permissions
    token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
        .with_identity("pipeline-test") \
        .with_name("Pipeline Test") \
        .with_grants(api.VideoGrants(
            room_join=True,
            room=ROOM_NAME,
            can_publish=True,
            can_subscribe=True,
        ))

    # Connect to room
    room = rtc.Room()
    await room.connect(LIVEKIT_URL, token.to_jwt())
    print(f"Connected to room: {ROOM_NAME}")

    # Create audio source for TTS playback
    audio_source = rtc.AudioSource(SAMPLE_RATE, 1)
    audio_track = rtc.LocalAudioTrack.create_audio_track("tts-audio", audio_source)

    # Publish our audio track
    options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    await room.local_participant.publish_track(audio_track, options)
    print("Published audio track for TTS playback")

    # Recording state
    audio_samples = []
    recording = False
    record_start = None
    record_duration = 5.0

    async def record_from_track(track, participant):
        nonlocal recording, record_start, audio_samples

        print(f"\n🎤 Recording from: {participant.identity}")
        print("   Speak now for 5 seconds...")

        audio_stream = rtc.AudioStream(track)
        recording = True
        record_start = time.time()
        audio_samples = []

        async for event in audio_stream:
            if not recording:
                break

            frame = event.frame
            samples = np.frombuffer(frame.data, dtype=np.int16)
            audio_samples.append(samples)

            elapsed = time.time() - record_start
            if elapsed >= record_duration:
                print(f"   Recorded {elapsed:.1f}s")
                recording = False
                break

    def on_track_subscribed(track, publication, participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.create_task(record_from_track(track, participant))

    room.on("track_subscribed", on_track_subscribed)

    # Check existing participants
    track_found = False
    for participant in room.remote_participants.values():
        print(f"Found participant: {participant.identity}")
        for publication in participant.track_publications.values():
            if publication.kind == rtc.TrackKind.KIND_AUDIO:
                print(f"  Audio track found, subscribed: {publication.subscribed}")
                if publication.track:
                    asyncio.create_task(record_from_track(publication.track, participant))
                    track_found = True

    # Wait for track subscription if not found yet
    print("\nWaiting for iPhone audio...")
    if not track_found:
        print("(Waiting for track subscription...)")

    timeout = 30
    start = time.time()
    while (not audio_samples or recording) and time.time() - start < timeout:
        await asyncio.sleep(0.1)

    if not audio_samples:
        print("❌ No audio received!")
        await room.disconnect()
        return

    # Combine and save audio
    audio_data = np.concatenate(audio_samples)
    print(f"\n📊 Audio: {len(audio_data)} samples, {len(audio_data)/SAMPLE_RATE:.1f}s")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        with wave.open(tmp.name, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(audio_data.tobytes())
        wav_path = tmp.name

    # STT
    print("\n🎯 Sending to Parakeet STT...")
    stt_start = time.time()

    # Build headers with API key if configured
    headers = {}
    if PARAKEET_API_KEY:
        headers["Authorization"] = f"Bearer {PARAKEET_API_KEY}"

    async with httpx.AsyncClient() as client:
        with open(wav_path, 'rb') as f:
            response = await client.post(
                STT_URL,
                files={"file": ("audio.wav", f, "audio/wav")},
                data={"model": "parakeet-tdt-0.6b-v3"},
                headers=headers,
                timeout=60.0
            )

    stt_time = time.time() - stt_start

    if response.status_code != 200:
        print(f"❌ STT Error: {response.status_code}")
        await room.disconnect()
        return

    text = response.json().get("text", "")
    print(f"   STT took: {stt_time:.2f}s")
    print(f"   📝 Transcription: \"{text}\"")

    if not text.strip():
        print("❌ Empty transcription")
        await room.disconnect()
        return

    # Generate response (echo for now)
    response_text = f"I heard you say: {text}"
    print(f"\n💬 Response: \"{response_text}\"")

    # TTS
    print("\n🔊 Generating TTS with Kokoro...")
    tts_start = time.time()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            TTS_URL,
            json={
                "model": "kokoro",
                "input": response_text,
                "voice": "af_sky",  # American female voice
                "response_format": "pcm"
            },
            timeout=60.0
        )

    tts_time = time.time() - tts_start

    if response.status_code != 200:
        print(f"❌ TTS Error: {response.status_code}")
        print(response.text)
        await room.disconnect()
        return

    print(f"   TTS took: {tts_time:.2f}s")

    # Send audio to iPhone via LiveKit
    print("\n📤 Sending audio to iPhone...")

    tts_audio = np.frombuffer(response.content, dtype=np.int16)
    print(f"   TTS samples: {len(tts_audio)} @ 24kHz")

    # Resample from 24kHz to 48kHz (LiveKit expects 48kHz)
    from scipy import signal
    tts_audio_48k = signal.resample(tts_audio, len(tts_audio) * 2).astype(np.int16)
    print(f"   Resampled: {len(tts_audio_48k)} @ 48kHz")

    # Send in chunks
    chunk_size = 960  # 20ms at 48kHz
    for i in range(0, len(tts_audio_48k), chunk_size):
        chunk = tts_audio_48k[i:i+chunk_size]
        if len(chunk) < chunk_size:
            chunk = np.pad(chunk, (0, chunk_size - len(chunk)))

        frame = rtc.AudioFrame.create(SAMPLE_RATE, 1, chunk_size)
        frame_data = np.frombuffer(frame.data, dtype=np.int16)
        frame_data[:] = chunk
        await audio_source.capture_frame(frame)
        await asyncio.sleep(0.015)  # ~20ms

    print("   ✅ Audio sent!")

    # Wait a bit for playback
    await asyncio.sleep(2)

    print("\n" + "=" * 50)
    print("Pipeline complete!")
    print(f"Total: STT {stt_time:.2f}s + TTS {tts_time:.2f}s")
    print("=" * 50)

    await room.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
