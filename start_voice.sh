#!/bin/bash
# Start all VoiceMode services for iPhone voice control
# Usage: ./start_voice.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================================="
echo "Starting VoiceMode Services"
echo "=================================================="

# 1. LiveKit Server
if curl -s http://localhost:7880/ > /dev/null 2>&1; then
    echo "✅ LiveKit already running on :7880"
else
    echo "🚀 Starting LiveKit server..."
    livekit-server --dev --bind 0.0.0.0 > /tmp/livekit.log 2>&1 &
    sleep 2
    if curl -s http://localhost:7880/ > /dev/null 2>&1; then
        echo "✅ LiveKit started"
    else
        echo "❌ LiveKit failed to start"
    fi
fi

# 2. Kokoro TTS
if curl -s http://localhost:8880/health > /dev/null 2>&1; then
    echo "✅ Kokoro TTS already running on :8880"
else
    echo "🚀 Starting Kokoro TTS..."
    uv run voicemode service start kokoro > /tmp/kokoro_start.log 2>&1 || true
    sleep 3
    if curl -s http://localhost:8880/health > /dev/null 2>&1; then
        echo "✅ Kokoro started"
    else
        echo "⚠️  Kokoro not running (start manually if needed)"
    fi
fi

# 3. Parakeet STT (uses miniconda python)
if curl -s http://localhost:2022/ > /dev/null 2>&1; then
    echo "✅ Parakeet STT already running on :2022"
else
    echo "🚀 Starting Parakeet STT..."
    /Users/maciej/miniconda3/bin/python parakeet_server.py 2022 > /tmp/parakeet.log 2>&1 &
    sleep 5
    if curl -s http://localhost:2022/ > /dev/null 2>&1; then
        echo "✅ Parakeet started"
    else
        echo "❌ Parakeet failed to start - check /tmp/parakeet.log"
    fi
fi

# 4. Voice Watcher (web UI)
if curl -s http://localhost:8890/api/sessions > /dev/null 2>&1; then
    echo "✅ Voice Watcher already running on :8890"
else
    echo "🚀 Starting Voice Watcher..."
    LIVEKIT_WS_URL="wss://livekit.drads.app" uv run python voice_watcher.py > /tmp/voice_watcher.log 2>&1 &
    sleep 2
    if curl -s http://localhost:8890/api/sessions > /dev/null 2>&1; then
        echo "✅ Voice Watcher started"
    else
        echo "❌ Voice Watcher failed - check /tmp/voice_watcher.log"
    fi
fi

# 5. Cloudflared tunnel
if pgrep -f "cloudflared tunnel" > /dev/null; then
    echo "✅ Cloudflared tunnel already running"
else
    echo "🚀 Starting Cloudflared tunnel..."
    cloudflared tunnel run seo-analyser > /tmp/cloudflared.log 2>&1 &
    sleep 3
    if pgrep -f "cloudflared tunnel" > /dev/null; then
        echo "✅ Cloudflared started"
    else
        echo "❌ Cloudflared failed - check /tmp/cloudflared.log"
    fi
fi

# 6. Voice Loop (connects to LiveKit, does STT/TTS)
if pgrep -f "voice_loop.py" > /dev/null; then
    echo "✅ Voice Loop already running"
else
    echo "🚀 Starting Voice Loop..."
    .venv/bin/python voice_loop.py > /tmp/voice_loop.log 2>&1 &
    sleep 2
    if pgrep -f "voice_loop.py" > /dev/null; then
        echo "✅ Voice Loop started"
    else
        echo "❌ Voice Loop failed - check /tmp/voice_loop.log"
    fi
fi

echo ""
echo "=================================================="
echo "Status Summary"
echo "=================================================="
echo "LiveKit:       http://localhost:7880"
echo "Kokoro TTS:    http://localhost:8880"
echo "Parakeet STT:  http://localhost:2022"
echo "Voice Watcher: http://localhost:8890"
echo ""
echo "External URLs (via Cloudflare):"
echo "  Voice Watcher: https://voice.drads.app"
echo "  LiveKit:       wss://livekit.drads.app"
echo ""
echo "To monitor voice loop: tail -f /tmp/voice_loop.log"
echo "=================================================="
