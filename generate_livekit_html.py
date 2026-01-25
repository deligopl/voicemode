#!/usr/bin/env python3
"""Generate LiveKit test HTML from template with tokens from environment.

Reads configuration from environment variables (or ~/.voicemode/voicemode.env)
and generates a ready-to-use HTML file with embedded token.

Usage:
    python generate_livekit_html.py [output_path]

    # Default output: livekit-test.local.html (in .gitignore)

Environment variables:
    LIVEKIT_URL          - WebSocket URL (default: ws://127.0.0.1:7880)
    LIVEKIT_WS_URL       - Override for browser (default: wss://livekit.drads.app)
    LIVEKIT_API_KEY      - API key (default: devkey)
    LIVEKIT_API_SECRET   - API secret (default: secret)
    VOICEMODE_LIVEKIT_ROOM - Room name (default: voicemode)
    LIVEKIT_TOKEN_DAYS   - Token validity in days (default: 365)
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path


def load_env_file():
    """Load environment from ~/.voicemode/voicemode.env if exists."""
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


def generate_token(api_key: str, api_secret: str, room: str, days: int) -> tuple[str, str]:
    """Generate LiveKit token and return (token, expiry_date)."""
    from livekit import api

    token = api.AccessToken(api_key, api_secret)
    token.with_identity("iphone-user")
    token.with_name("iPhone")
    token.with_ttl(timedelta(days=days))
    token.with_grants(api.VideoGrants(
        room_join=True,
        room=room,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True
    ))

    expiry = datetime.now() + timedelta(days=days)
    return token.to_jwt(), expiry.strftime("%Y-%m-%d")


def generate_html(output_path: str = None):
    """Generate HTML from template with current configuration."""
    # Load env file first
    load_env_file()

    # Get configuration
    api_key = os.getenv("LIVEKIT_API_KEY", "devkey")
    api_secret = os.getenv("LIVEKIT_API_SECRET", "secret")
    room = os.getenv("VOICEMODE_LIVEKIT_ROOM", "voicemode")
    days = int(os.getenv("LIVEKIT_TOKEN_DAYS", "365"))

    # WebSocket URL for browser (might be different from local server URL)
    ws_url = os.getenv("LIVEKIT_WS_URL")
    if not ws_url:
        # Try to derive from LIVEKIT_URL
        livekit_url = os.getenv("LIVEKIT_URL", "ws://127.0.0.1:7880")
        if "127.0.0.1" in livekit_url or "localhost" in livekit_url:
            # Local URL won't work from iPhone, use default
            ws_url = "wss://livekit.drads.app"
        else:
            ws_url = livekit_url.replace("ws://", "wss://")

    # Generate token
    print(f"Generating token...")
    print(f"  API Key: {api_key}")
    print(f"  Room: {room}")
    print(f"  Valid for: {days} days")

    token, expiry = generate_token(api_key, api_secret, room, days)

    # Find template
    script_dir = Path(__file__).parent
    template_path = script_dir / "livekit-test.html.template"

    if not template_path.exists():
        print(f"Error: Template not found at {template_path}")
        sys.exit(1)

    # Read template
    template = template_path.read_text()

    # Replace placeholders
    html = template.replace("{{LIVEKIT_WS_URL}}", ws_url)
    html = html.replace("{{LIVEKIT_ROOM}}", room)
    html = html.replace("{{LIVEKIT_TOKEN}}", token)
    html = html.replace("{{GENERATED_DATE}}", datetime.now().strftime("%Y-%m-%d %H:%M"))
    html = html.replace("{{TOKEN_EXPIRY}}", expiry)

    # Output path
    if output_path is None:
        output_path = script_dir / "voice.html"
    else:
        output_path = Path(output_path)

    # Write output
    output_path.write_text(html)

    print(f"\n✅ Generated: {output_path}")
    print(f"   Server: {ws_url}")
    print(f"   Token expires: {expiry}")
    print(f"\nServe this file via your web server (e.g., cloudflared tunnel)")


if __name__ == "__main__":
    output = sys.argv[1] if len(sys.argv) > 1 else None
    generate_html(output)
