#!/usr/bin/env python3
"""Start all VoiceMode services for iPhone voice control.

Usage:
    python voicemode_start.py [--generate-only]

This script:
1. Generates voice.html with fresh token
2. Starts Parakeet STT server (if not running)
3. Starts Kokoro TTS server (if not running)
4. Starts LiveKit server (if not running)
5. Shows status of all services

Requirements:
    - ~/.voicemode/voicemode.env with configuration
    - cloudflared tunnel running (for iPhone access)
"""

import os
import sys
import subprocess
import socket
import time
from pathlib import Path


def load_env():
    """Load environment from ~/.voicemode/voicemode.env."""
    env_file = Path.home() / ".voicemode" / "voicemode.env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and value and key not in os.environ:
                        os.environ[key] = value


def is_port_open(port: int) -> bool:
    """Check if a port is open (service running)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except:
        return False


def check_service(name: str, port: int) -> bool:
    """Check if a service is running."""
    running = is_port_open(port)
    status = "✅ running" if running else "❌ not running"
    print(f"  {name}: {status} (port {port})")
    return running


def start_parakeet():
    """Start Parakeet STT server."""
    port = int(os.getenv("VOICEMODE_WHISPER_PORT", "2022"))
    if is_port_open(port):
        print(f"  Parakeet already running on port {port}")
        return True

    print(f"  Starting Parakeet STT on port {port}...")
    script_dir = Path(__file__).parent
    parakeet_script = script_dir / "parakeet_server.py"

    if not parakeet_script.exists():
        print(f"  ❌ parakeet_server.py not found")
        return False

    # Start in background
    subprocess.Popen(
        [sys.executable, str(parakeet_script), str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )

    # Wait for startup
    for _ in range(30):
        time.sleep(0.5)
        if is_port_open(port):
            print(f"  ✅ Parakeet started on port {port}")
            return True

    print(f"  ❌ Parakeet failed to start")
    return False


def start_kokoro():
    """Start Kokoro TTS server via voicemode CLI."""
    port = int(os.getenv("VOICEMODE_KOKORO_PORT", "8880"))
    if is_port_open(port):
        print(f"  Kokoro already running on port {port}")
        return True

    print(f"  Starting Kokoro TTS on port {port}...")

    # Try to start via voicemode CLI
    try:
        result = subprocess.run(
            ["voicemode", "service", "start", "kokoro"],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            # Wait for startup
            for _ in range(30):
                time.sleep(0.5)
                if is_port_open(port):
                    print(f"  ✅ Kokoro started on port {port}")
                    return True
    except FileNotFoundError:
        print("  ❌ voicemode CLI not found")
    except subprocess.TimeoutExpired:
        print("  ❌ Kokoro start timed out")

    print(f"  ❌ Kokoro failed to start")
    return False


def start_livekit():
    """Check LiveKit server (must be started separately)."""
    port = int(os.getenv("VOICEMODE_LIVEKIT_PORT", "7880"))
    if is_port_open(port):
        print(f"  LiveKit already running on port {port}")
        return True

    print(f"  ⚠️  LiveKit not running on port {port}")
    print(f"     Start it manually with: livekit-server --dev")
    return False


def generate_html():
    """Generate voice.html with token."""
    print("\n📄 Generating voice.html...")
    script_dir = Path(__file__).parent
    generator = script_dir / "generate_livekit_html.py"

    if not generator.exists():
        print(f"  ❌ generate_livekit_html.py not found")
        return False

    result = subprocess.run(
        [sys.executable, str(generator)],
        capture_output=True,
        text=True
    )

    if result.returncode == 0:
        print(result.stdout)
        return True
    else:
        print(f"  ❌ Failed to generate HTML")
        print(result.stderr)
        return False


def main():
    print("=" * 50)
    print("VoiceMode Start")
    print("=" * 50)

    # Load environment
    load_env()

    generate_only = "--generate-only" in sys.argv

    # Generate HTML first
    if not generate_html():
        print("\n❌ Failed to generate voice.html")
        return 1

    if generate_only:
        print("\n✅ HTML generated (--generate-only mode)")
        return 0

    # Check/start services
    print("\n🔧 Services:")

    livekit_ok = start_livekit()
    parakeet_ok = start_parakeet()
    kokoro_ok = start_kokoro()

    # Summary
    print("\n" + "=" * 50)
    if livekit_ok and parakeet_ok and kokoro_ok:
        print("✅ All services running!")
        print("\nAccess from iPhone:")
        ws_url = os.getenv("LIVEKIT_WS_URL", "wss://livekit.drads.app")
        print(f"  1. Open https://voicetest.drads.app")
        print(f"  2. Login via Cloudflare Access")
        print(f"  3. Click Connect → Enable Mic")
        print(f"\nLiveKit WebSocket: {ws_url}")
    else:
        print("⚠️  Some services not running")
        if not livekit_ok:
            print("   - Start LiveKit: livekit-server --dev")

    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
