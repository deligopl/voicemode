"""LiveKit remote voice CLI commands.

Test and manage LiveKit remote audio transport for voice conversations
from remote devices (e.g., iPhone via Safari).
"""

import asyncio
import click
import logging
import os

logger = logging.getLogger("voicemode.livekit_remote")


@click.group()
def livekit_remote():
    """LiveKit remote voice commands for iPhone/remote device support."""
    pass


@livekit_remote.command("test")
@click.option("--url", default=None, help="LiveKit server URL (ws://...)")
@click.option("--room", default="voicemode", help="Room name to join")
@click.option("--duration", default=10.0, help="Test duration in seconds")
def test_connection(url: str, room: str, duration: float):
    """Test LiveKit connection and audio transport.

    This command connects to a LiveKit room and waits for a remote participant
    to join. Use this to verify your LiveKit setup before using voice mode.

    Example:
        voicemode livekit-remote test --url ws://localhost:7880
    """
    asyncio.run(_test_connection(url, room, duration))


async def _test_connection(url: str, room: str, duration: float):
    """Async implementation of connection test."""
    from voice_mode.audio_transport import LiveKitAudioTransport
    from voice_mode.config import LIVEKIT_URL

    url = url or LIVEKIT_URL

    click.echo(f"Connecting to LiveKit server: {url}")
    click.echo(f"Room: {room}")

    transport = LiveKitAudioTransport(url=url, room_name=room)

    try:
        await transport.connect()
        click.echo(click.style("Connected!", fg="green"))
        click.echo(f"\nWaiting {duration}s for remote participants...")
        click.echo("Open LiveKit playground or connect from iPhone to test audio.")
        click.echo(f"\nPlayground URL: https://agents-playground.livekit.io")
        click.echo(f"Or connect via: {url.replace('ws://', 'http://').replace('wss://', 'https://')}")

        await asyncio.sleep(duration)

        click.echo("\nTest complete.")

    except Exception as e:
        click.echo(click.style(f"Connection failed: {e}", fg="red"))
        raise click.Abort()
    finally:
        await transport.disconnect()


@livekit_remote.command("echo")
@click.option("--url", default=None, help="LiveKit server URL")
@click.option("--room", default="voicemode", help="Room name")
def echo_test(url: str, room: str):
    """Echo test - speak and hear your voice back.

    Connects to LiveKit, records audio from remote participant,
    and plays it back. Useful for testing end-to-end audio flow.
    """
    asyncio.run(_echo_test(url, room))


async def _echo_test(url: str, room: str):
    """Echo test implementation."""
    from voice_mode.audio_transport import LiveKitAudioTransport, AudioConfig
    from voice_mode.config import LIVEKIT_URL

    url = url or LIVEKIT_URL
    config = AudioConfig(sample_rate=24000, channels=1)

    click.echo(f"Connecting to {url}, room: {room}")

    transport = LiveKitAudioTransport(url=url, room_name=room)

    try:
        await transport.connect()
        click.echo(click.style("Connected!", fg="green"))

        click.echo("\nWaiting for remote participant to speak...")
        click.echo("Recording for 5 seconds...")

        audio = await transport.record(5.0, config)

        if len(audio) > 0:
            click.echo(f"Recorded {len(audio)} samples")
            click.echo("Playing back to remote...")
            await transport.play(audio, config)
            click.echo("Done!")
        else:
            click.echo(click.style("No audio received. Is anyone connected?", fg="yellow"))

    except Exception as e:
        click.echo(click.style(f"Error: {e}", fg="red"))
    finally:
        await transport.disconnect()


@livekit_remote.command("voice-loop")
@click.option("--url", default=None, help="LiveKit server URL")
@click.option("--room", default="voicemode", help="Room name")
def voice_loop(url: str, room: str):
    """Start a voice conversation loop via LiveKit.

    This is the main command for remote voice mode. It:
    1. Connects to LiveKit room
    2. Waits for your voice input from iPhone/remote device
    3. Transcribes with STT
    4. Returns text (for Claude Code to process)
    5. Speaks TTS response back to you

    Use this with VOICEMODE_AUDIO_TRANSPORT=livekit for full integration.
    """
    asyncio.run(_voice_loop(url, room))


async def _voice_loop(url: str, room: str):
    """Voice loop implementation."""
    from voice_mode.audio_transport import LiveKitAudioTransport, AudioConfig
    from voice_mode.config import LIVEKIT_URL

    url = url or LIVEKIT_URL
    config = AudioConfig(sample_rate=24000, channels=1)

    click.echo(f"Starting voice loop on {url}, room: {room}")
    click.echo("Connect from your iPhone using LiveKit playground or web client")
    click.echo("Press Ctrl+C to stop\n")

    transport = LiveKitAudioTransport(url=url, room_name=room)

    try:
        await transport.connect()
        click.echo(click.style("Connected! Waiting for voice input...\n", fg="green"))

        while True:
            # Record with VAD
            audio, speech_detected = await transport.record_with_vad(
                max_duration=30.0,
                min_duration=1.0,
                config=config,
                silence_threshold_ms=1500
            )

            if speech_detected and len(audio) > 0:
                click.echo(f"Received {len(audio)} samples of speech")

                # Here we would do STT and pass to Claude
                # For now just echo back
                click.echo("Echoing back...")
                await transport.play(audio, config)
                click.echo("Ready for next input...\n")
            else:
                await asyncio.sleep(0.1)

    except KeyboardInterrupt:
        click.echo("\n\nStopping voice loop...")
    except Exception as e:
        click.echo(click.style(f"Error: {e}", fg="red"))
    finally:
        await transport.disconnect()
        click.echo("Disconnected.")


@livekit_remote.command("info")
def show_info():
    """Show LiveKit configuration info."""
    from voice_mode.config import (
        AUDIO_TRANSPORT,
        LIVEKIT_URL,
        LIVEKIT_API_KEY,
        LIVEKIT_ROOM,
        LIVEKIT_PORT,
    )

    click.echo("LiveKit Configuration:")
    click.echo(f"  Audio Transport: {AUDIO_TRANSPORT}")
    click.echo(f"  Server URL: {LIVEKIT_URL}")
    click.echo(f"  API Key: {LIVEKIT_API_KEY}")
    click.echo(f"  Default Room: {LIVEKIT_ROOM}")
    click.echo(f"  Port: {LIVEKIT_PORT}")
    click.echo()
    click.echo("To enable LiveKit transport, set:")
    click.echo("  export VOICEMODE_AUDIO_TRANSPORT=livekit")
