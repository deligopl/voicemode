# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## iPhone Voice Control (Voice Watcher)

This session can be controlled via voice from an iPhone. When voice_loop.py is running:
- **Input**: User speech is automatically transcribed and sent to this session (no action needed)
- **Output**: Use `uv run voicemode converse -m "message" --no-wait` to speak to user
- **Permission prompts**: Handled automatically by voice hook (user says "yes" or "no")

### How It Works

```
iPhone speaks → voice_loop.py transcribes → sends to tmux → appears as input here
Claude responds → TTS command → LiveKit → iPhone hears it
```

**No skill loading required** - voice I/O is handled externally by voice_loop.py and hooks.

### Speaking to User

To say something to the user via TTS:
```bash
uv run voicemode converse -m "Your message here" --no-wait
```

### Available Skills (optional)

These skills provide additional functionality but are **not required** for basic voice operation:

| Skill | When to use |
|-------|-------------|
| `/session-watcher` | Check status of other AoE sessions, send commands to them |
| `/voicemode-dj` | Background music control during sessions |

### Quick Start

Start all voice services with one command:
```bash
./start_voice.sh
```

Then start the voice loop for this session:
```bash
.venv/bin/python voice_loop.py --tmux aoe_Voice_mode_73b1e342 > /tmp/voice_loop.log 2>&1 &
```

Connect from iPhone: **https://voice.drads.app**

### Service Management

**Check service status:**
```bash
# All services at once
curl -s http://localhost:7880/ && echo "LiveKit: OK"
curl -s http://localhost:8880/health && echo "Kokoro: OK"
curl -s http://localhost:2022/ && echo "Parakeet: OK"
curl -s http://localhost:8890/api/sessions && echo "Voice Watcher: OK"
pgrep -f "voice_loop.py" && echo "Voice Loop: OK"
pgrep -f "cloudflared" && echo "Cloudflared: OK"
```

**Start individual services:**
```bash
# LiveKit server
livekit-server --dev --bind 0.0.0.0 > /tmp/livekit.log 2>&1 &

# Kokoro TTS
uv run voicemode service start kokoro

# Parakeet STT (uses miniconda python)
/Users/maciej/miniconda3/bin/python parakeet_server.py 2022 > /tmp/parakeet.log 2>&1 &

# Voice Watcher (web UI)
LIVEKIT_WS_URL="wss://livekit.drads.app" uv run python voice_watcher.py > /tmp/voice_watcher.log 2>&1 &

# Cloudflared tunnel
cloudflared tunnel run seo-analyser > /tmp/cloudflared.log 2>&1 &

# Voice Loop (connects LiveKit to this session)
.venv/bin/python voice_loop.py --tmux SESSION_NAME > /tmp/voice_loop.log 2>&1 &
```

**Restart voice loop (if connection lost):**
```bash
pkill -f voice_loop.py
.venv/bin/python voice_loop.py --tmux aoe_Voice_mode_73b1e342 > /tmp/voice_loop.log 2>&1 &
```

**Check logs:**
```bash
tail -f /tmp/voice_loop.log      # Voice transcriptions
tail -f /tmp/voice_watcher.log   # Web UI server
tail -f /tmp/livekit.log         # LiveKit server
tail -f /tmp/cloudflared.log     # Tunnel status
```

### Voice Permission Approval

When Claude needs permission for a tool (e.g., Bash command), the system will:
1. Speak the permission request through iPhone
2. Wait for your voice response
3. Approve or deny based on what you say

**To approve:** Say "yes", "tak", "okay", "approve", "dawaj", "dobra"
**To deny:** Say "no", "nie", "deny", "stop", "cancel"

The hook is configured in `.claude/settings.json` and uses `.claude/hooks/voice-permission.sh`.

### Architecture

```
iPhone (Safari)
    ↓ WebRTC audio
voice.drads.app (Voice Watcher UI)
    ↓ LiveKit room "voicemode"
voice_loop.py (receives audio, does STT)
    ↓ tmux send-keys
Claude Code session (this conversation)
    ↓ voicemode converse (TTS)
LiveKit room "voicemode"
    ↓ WebRTC audio
iPhone (hears response)
```

### Troubleshooting

**No audio from iPhone:**
- Check if "Click to Talk" is pressed (red button)
- Verify voice_loop.py is running and sees "iphone-user"
- Check `tail -f /tmp/voice_loop.log`

**Can't hear TTS responses:**
- Verify iPhone is connected (green "Connected" status)
- Check Kokoro is running: `curl http://localhost:8880/health`
- Test TTS: `uv run voicemode converse -m "Test" --no-wait`

**Permission hook not working:**
- Check if hook is loaded: `/hooks` command in Claude Code
- Verify voice_loop.py is running
- Check `/tmp/voice-permission-hook.log` for details

**Services keep dying:**
- Run `./start_voice.sh` to restart everything
- Check individual logs in `/tmp/`

## Project Overview

VoiceMode is a Python package that provides voice interaction capabilities for AI assistants through the Model Context Protocol (MCP). It enables natural voice conversations with Claude Code and other AI coding assistants by integrating speech-to-text (STT) and text-to-speech (TTS) services.

## Key Commands

### Development & Testing
```bash
# Install in development mode with dependencies
make dev-install

# Run all unit tests
make test
# Or directly: uv run pytest tests/ -v --tb=short

# Run specific test
uv run pytest tests/test_voice_mode.py -v

# Clean build artifacts
make clean
```

### Building & Publishing
```bash
# Build Python package
make build-package

# Build development version (auto-versioned)
make build-dev  

# Test package installation
make test-package

# Release workflow (bumps version, tags, pushes)
make release
```

### Documentation
```bash
# Serve docs locally at http://localhost:8000
make docs-serve

# Build documentation site
make docs-build

# Check docs for errors (strict mode)
make docs-check
```

## Architecture Overview

### Core Components

1. **MCP Server (`voice_mode/server.py`)**
   - FastMCP-based server providing voice tools via stdio transport
   - Auto-imports all tools, prompts, and resources
   - Handles FFmpeg availability checks and logging setup

2. **Tool System (`voice_mode/tools/`)**
   - **converse.py**: Primary voice conversation tool with TTS/STT integration
   - **service.py**: Unified service management for Whisper/Kokoro
   - **providers.py**: Provider discovery and registry management
   - **devices.py**: Audio device detection and management
   - Services subdirectory contains install/uninstall tools for Whisper and Kokoro
   - See [Tool Loading Architecture](docs/reference/tool-loading-architecture.md) for internal details

3. **Provider System (`voice_mode/providers.py`)**
   - Dynamic discovery of OpenAI-compatible TTS/STT endpoints
   - Health checking and failover support
   - Maintains registry of available voice services

4. **Configuration (`voice_mode/config.py`)**
   - Environment-based configuration with sensible defaults
   - Support for voice preference files (project/user level)
   - Audio format configuration (PCM, MP3, WAV, FLAC, AAC, Opus)

5. **Resources (`voice_mode/resources/`)**
   - MCP resources exposed for client access
   - Statistics, configuration, changelog, and version information
   - Whisper model management

### Service Architecture

The project supports multiple voice service backends:
- **OpenAI API**: Cloud-based TTS/STT (requires API key)
- **Whisper.cpp**: Local speech-to-text service
- **Kokoro**: Local text-to-speech with multiple voices

Services can be installed and managed through MCP tools, with automatic service discovery and health checking.

### Key Design Patterns

1. **OpenAI API Compatibility**: All voice services expose OpenAI-compatible endpoints, enabling transparent switching between providers
2. **Dynamic Tool Discovery**: Tools are auto-imported from the tools directory structure
3. **Failover Support**: Automatic fallback between services based on availability
4. **Local Microphone Transport**: Direct audio capture via PyAudio for voice interactions
5. **Audio Format Negotiation**: Automatic format validation against provider capabilities

## Development Notes

- The project uses `uv` for package management (not pip directly)
- Python 3.10+ is required
- FFmpeg is required for audio processing
- The project follows a modular architecture with FastMCP patterns
- Service installation tools handle platform-specific setup (launchd on macOS, systemd on Linux)
- Event logging and conversation logging are available for debugging
- WebRTC VAD is used for silence detection when available

## Testing

- Unit tests: `tests/` - run with `make test`
- Manual tests: `tests/manual/` - require user interaction

## Logging

Logs are stored in `~/.voicemode/`:
- `logs/conversations/` - Voice exchange history (JSONL)
- `logs/events/` - Operational events and errors
- `audio/` - Saved TTS/STT audio files
- `voicemode.env` - User configuration

## See Also

- **[skills/voicemode/SKILL.md](skills/voicemode/SKILL.md)** - Voice interaction usage and MCP tools
- **[docs/tutorials/getting-started.md](docs/tutorials/getting-started.md)** - Installation guide
- **[docs/guides/configuration.md](docs/guides/configuration.md)** - Configuration reference
- **[docs/concepts/architecture.md](docs/concepts/architecture.md)** - Detailed architecture