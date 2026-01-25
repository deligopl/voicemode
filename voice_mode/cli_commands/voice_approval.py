"""Voice approval CLI command for Claude Code permission prompts.

This module provides voice-based approval for Claude Code permission prompts.
When Claude Code asks for permission (e.g., "Run command: npm install?"),
this command speaks the question and listens for voice approval.

Usage:
    # From Notification hook (reads JSON from stdin)
    echo '{"notification_type":"permission_prompt","message":"Run command?"}' | voicemode voice-approval

    # With tmux session for auto-response
    voicemode voice-approval --tmux-session claude

    # Test mode
    voicemode voice-approval --test "Run npm install?"
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from typing import Optional

import click

logger = logging.getLogger("voicemode.voice_approval")


def parse_approval_response(text: str) -> Optional[bool]:
    """Parse user's voice response to determine approval.

    Args:
        text: Transcribed user response

    Returns:
        True if approved, False if denied, None if unclear
    """
    text_lower = text.lower().strip()

    # Affirmative responses (Polish and English)
    affirmative = [
        "tak", "yes", "yeah", "yep", "sure", "ok", "okay", "go ahead",
        "do it", "proceed", "zatwierdź", "zrób to", "dawaj", "jasne",
        "oczywiście", "pewnie", "no", "dobra", "git", "spoko",
        "approve", "approved", "confirm", "confirmed", "accept"
    ]

    # Negative responses
    negative = [
        "nie", "no", "nope", "don't", "stop", "cancel", "abort",
        "nie rób", "anuluj", "przerwij", "odmów", "odrzuć",
        "deny", "denied", "reject", "rejected", "refuse"
    ]

    for word in affirmative:
        if word in text_lower:
            return True

    for word in negative:
        if word in text_lower:
            return False

    return None


def send_tmux_response(session: str, approved: bool) -> bool:
    """Send approval response to tmux session.

    Args:
        session: tmux session name
        approved: True to send 'y', False to send 'n'

    Returns:
        True if successful
    """
    response = "y" if approved else "n"

    try:
        # Check if session exists
        result = subprocess.run(
            ["tmux", "has-session", "-t", session],
            capture_output=True
        )
        if result.returncode != 0:
            logger.error(f"tmux session '{session}' not found")
            return False

        # Send the response key
        subprocess.run(
            ["tmux", "send-keys", "-t", session, response, "Enter"],
            check=True
        )
        logger.info(f"Sent '{response}' to tmux session '{session}'")
        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to send tmux response: {e}")
        return False
    except FileNotFoundError:
        logger.error("tmux not found. Please install tmux.")
        return False


async def ask_for_approval(
    message: str,
    use_livekit: bool = False
) -> Optional[bool]:
    """Ask user for voice approval.

    Args:
        message: The permission prompt message
        use_livekit: Use LiveKit for remote audio (iPhone)

    Returns:
        True if approved, False if denied, None if no response
    """
    from voice_mode.config import AUDIO_TRANSPORT, SAMPLE_RATE, CHANNELS
    from voice_mode.audio_transport import AudioConfig, ensure_connected

    # Format the question
    question = f"Claude pyta: {message}. Zatwierdzić? Powiedz tak lub nie."

    # Determine audio transport
    transport_type = "livekit" if use_livekit else AUDIO_TRANSPORT

    if transport_type == "livekit":
        # Use LiveKit for remote audio
        config = AudioConfig(sample_rate=SAMPLE_RATE, channels=CHANNELS)
        transport = await ensure_connected()

        # For now, we need TTS through LiveKit too
        # This is a simplified version - full implementation would use TTS
        logger.info(f"Voice approval via LiveKit: {question}")

        # Record response
        audio, speech_detected = await transport.record_with_vad(
            max_duration=10.0,
            min_duration=1.0,
            config=config,
            silence_threshold_ms=1500
        )

        if not speech_detected:
            return None

        # STT
        from voice_mode.tools.converse import speech_to_text
        import numpy as np
        result = await speech_to_text(audio, save_audio=False)

        if result and result.get("text"):
            text = result["text"]
            logger.info(f"User said: {text}")
            return parse_approval_response(text)

        return None

    else:
        # Use local audio via voicemode converse
        # This is a workaround - ideally we'd call the converse tool directly
        try:
            # Use subprocess to call voicemode CLI
            result = subprocess.run(
                [
                    "voicemode", "converse",
                    "--message", question,
                    "--wait",
                    "--duration", "10"
                ],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                # Parse output to find transcription
                output = result.stdout
                # Look for "Heard:" or similar marker
                for line in output.split("\n"):
                    if "Heard:" in line or "heard:" in line:
                        text = line.split(":", 1)[1].strip()
                        return parse_approval_response(text)

            return None

        except subprocess.TimeoutExpired:
            logger.warning("Voice approval timed out")
            return None
        except Exception as e:
            logger.error(f"Voice approval failed: {e}")
            return None


@click.command()
@click.option("--tmux-session", "-t", default=None,
              help="tmux session name to send response to")
@click.option("--livekit", is_flag=True, default=False,
              help="Use LiveKit for remote audio (iPhone)")
@click.option("--test", default=None,
              help="Test with a specific message instead of reading from stdin")
@click.option("--dry-run", is_flag=True, default=False,
              help="Don't actually send tmux response")
@click.option("--auto-approve", is_flag=True, default=False,
              help="Automatically approve without asking (for testing)")
@click.option("--timeout", default=15.0, type=float,
              help="Timeout for voice response in seconds")
def voice_approval(
    tmux_session: str,
    livekit: bool,
    test: str,
    dry_run: bool,
    auto_approve: bool,
    timeout: float
):
    """Voice-based approval for Claude Code permission prompts.

    Reads permission prompt from stdin (JSON from Notification hook),
    asks user via voice, and optionally sends response to tmux session.

    Examples:

        # From Notification hook
        echo '{"notification_type":"permission_prompt","message":"Run npm?"}' | \\
            voicemode voice-approval --tmux-session claude

        # Test mode
        voicemode voice-approval --test "Edit file main.py?" --dry-run

        # With LiveKit (for iPhone)
        voicemode voice-approval --livekit --tmux-session claude
    """
    message = None

    # Get message from test option or stdin
    if test:
        message = test
    elif not sys.stdin.isatty():
        try:
            json_input = sys.stdin.read()
            data = json.loads(json_input)

            # Check if this is a permission prompt
            notification_type = data.get("notification_type", "")
            if notification_type != "permission_prompt":
                click.echo(f"Not a permission prompt (type: {notification_type}), skipping")
                return

            message = data.get("message", "")

        except json.JSONDecodeError as e:
            click.echo(f"Failed to parse JSON from stdin: {e}", err=True)
            return

    if not message:
        click.echo("No message provided. Use --test or pipe JSON to stdin.", err=True)
        return

    click.echo(f"Permission prompt: {message}")

    if auto_approve:
        click.echo("Auto-approve enabled, approving...")
        approved = True
    else:
        click.echo("Asking for voice approval...")

        try:
            approved = asyncio.run(
                asyncio.wait_for(
                    ask_for_approval(message, use_livekit=livekit),
                    timeout=timeout
                )
            )
        except asyncio.TimeoutError:
            click.echo("Voice approval timed out")
            approved = None

    if approved is None:
        click.echo(click.style("No clear response received", fg="yellow"))
        return

    if approved:
        click.echo(click.style("Approved!", fg="green"))
    else:
        click.echo(click.style("Denied!", fg="red"))

    # Send to tmux if configured
    if tmux_session and not dry_run:
        if send_tmux_response(tmux_session, approved):
            click.echo(f"Response sent to tmux session '{tmux_session}'")
        else:
            click.echo(f"Failed to send response to tmux", err=True)
    elif dry_run:
        click.echo(f"[Dry run] Would send '{'y' if approved else 'n'}' to tmux")
