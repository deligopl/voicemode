#!/usr/bin/env python3
"""Voice Watcher - monitor Claude Code sessions via voice.

Reads Agent of Empires sessions.json (READ-ONLY) and provides:
- List of sessions with status
- Voice interface to ask about sessions
- Ability to connect to sessions via tmux
- LiveKit integration for voice from iPhone

IMPORTANT: This script ONLY READS sessions.json, never writes to it.
"""

import json
import os
import re
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass

from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
import uvicorn

# Load environment from ~/.voicemode/voicemode.env
def load_env():
    env_file = Path.home() / ".voicemode" / "voicemode.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and value and key not in os.environ:
                    os.environ[key] = value

load_env()

# Paths
AOE_SESSIONS_FILE = Path.home() / ".agent-of-empires" / "profiles" / "default" / "sessions.json"
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# LiveKit config
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secret")
LIVEKIT_WS_URL = os.getenv("LIVEKIT_WS_URL", "wss://livekit.drads.app")
LIVEKIT_ROOM = os.getenv("VOICEMODE_LIVEKIT_ROOM", "voicemode")

app = FastAPI(title="Voice Watcher")


@dataclass
class Session:
    id: str
    title: str
    project_path: str
    group_path: str
    status: str  # idle, waiting, running
    user_active: bool
    created_at: str

    @property
    def tmux_name(self) -> str:
        """Generate tmux session name (matches AoE pattern)."""
        # Sanitize title: replace spaces with _, remove special chars
        safe_title = re.sub(r'[^a-zA-Z0-9_]', '', self.title.replace(' ', '_'))
        # Truncate if too long
        if len(safe_title) > 20:
            safe_title = safe_title[:20]
        return f"aoe_{safe_title}_{self.id[:8]}"

    @property
    def status_emoji(self) -> str:
        return {
            "waiting": "⏳",
            "running": "🔄",
            "idle": "💤"
        }.get(self.status, "❓")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "project_path": self.project_path,
            "group_path": self.group_path,
            "status": self.status,
            "status_emoji": self.status_emoji,
            "user_active": self.user_active,
            "created_at": self.created_at,
            "tmux_name": self.tmux_name,
        }


def read_sessions() -> list[Session]:
    """Read sessions from AoE config (READ-ONLY)."""
    if not AOE_SESSIONS_FILE.exists():
        return []

    try:
        with open(AOE_SESSIONS_FILE, 'r') as f:
            data = json.load(f)

        return [
            Session(
                id=s.get("id", ""),
                title=s.get("title", "Unknown"),
                project_path=s.get("project_path", ""),
                group_path=s.get("group_path", ""),
                status=s.get("status", "idle"),
                user_active=s.get("user_active", False),
                created_at=s.get("created_at", ""),
            )
            for s in data
        ]
    except Exception as e:
        print(f"Error reading sessions: {e}")
        return []


def get_session_by_id(session_id: str) -> Optional[Session]:
    """Get a specific session by ID."""
    sessions = read_sessions()
    for s in sessions:
        if s.id == session_id:
            return s
    return None


def check_tmux_session(tmux_name: str) -> bool:
    """Check if tmux session exists."""
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", tmux_name],
            capture_output=True
        )
        return result.returncode == 0
    except:
        return False


def project_path_to_claude_dir(project_path: str) -> str:
    """Convert project path to Claude directory name.

    Example: /Users/maciej/work/greenroom/greenroom-next
          -> -Users-maciej-work-greenroom-greenroom-next
    """
    return project_path.replace("/", "-")


def get_claude_session_file(project_path: str) -> Optional[Path]:
    """Find Claude Code session file for a project."""
    # Claude stores sessions in ~/.claude/projects/{path-with-dashes}/{session_id}.jsonl

    if not CLAUDE_PROJECTS_DIR.exists():
        return None

    # Convert project path to Claude directory name
    claude_dir_name = project_path_to_claude_dir(project_path)
    project_dir = CLAUDE_PROJECTS_DIR / claude_dir_name

    if not project_dir.exists():
        return None

    # Find most recently modified .jsonl file
    jsonl_files = list(project_dir.glob("*.jsonl"))
    if not jsonl_files:
        return None

    return max(jsonl_files, key=lambda p: p.stat().st_mtime)


def read_last_messages(session_file: Path, count: int = 10) -> list[dict]:
    """Read last N messages from a Claude session file."""
    if not session_file.exists():
        return []

    messages = []
    try:
        with open(session_file, 'r') as f:
            for line in f:
                try:
                    msg = json.loads(line.strip())
                    messages.append(msg)
                except:
                    continue

        return messages[-count:] if len(messages) > count else messages
    except Exception as e:
        print(f"Error reading session file: {e}")
        return []


def format_message_for_display(msg: dict) -> Optional[dict]:
    """Format a Claude session message for display."""
    msg_type = msg.get("type")

    if msg_type == "user":
        # User message - extract text content or tool result
        message = msg.get("message", {})
        content = message.get("content", [])
        text = ""
        is_tool_result = False
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    block_text = block.get("text", "")
                    # Skip system interruption messages
                    if block_text.startswith("[Request interrupted"):
                        continue
                    text += block_text
                elif block.get("type") == "tool_result":
                    # This is a tool result - show abbreviated version
                    is_tool_result = True
                    result = block.get("content", "")
                    if isinstance(result, str) and len(result) > 100:
                        text = f"[Output: {result[:100]}...]"
                    elif result:
                        text = f"[Output: {result}]"
            elif isinstance(block, str):
                text += block
        if text:
            return {
                "role": "tool_result" if is_tool_result else "user",
                "content": text[:500] + ("..." if len(text) > 500 else ""),
                "timestamp": msg.get("timestamp"),
            }

    elif msg_type == "assistant":
        # Assistant message - extract text content
        message = msg.get("message", {})
        content = message.get("content", [])
        text = ""
        tool_uses = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text += block.get("text", "")
                elif block.get("type") == "tool_use":
                    tool_name = block.get("name", "unknown")
                    tool_uses.append(tool_name)
        # Always include assistant messages with tools, even without text
        if text or tool_uses:
            display_content = text[:500] + ("..." if len(text) > 500 else "")
            if not display_content and tool_uses:
                display_content = f"Using: {', '.join(tool_uses)}"
            return {
                "role": "assistant",
                "content": display_content,
                "tools": tool_uses if tool_uses else None,
                "timestamp": msg.get("timestamp"),
            }

    return None


def get_session_messages(project_path: str, count: int = 10) -> list[dict]:
    """Get formatted messages for a project's Claude session."""
    session_file = get_claude_session_file(project_path)
    if not session_file:
        return []

    raw_messages = read_last_messages(session_file, count * 3)  # Read more to filter
    formatted = []
    for msg in raw_messages:
        formatted_msg = format_message_for_display(msg)
        if formatted_msg:
            formatted.append(formatted_msg)

    return formatted[-count:]  # Return last N formatted


# === API Endpoints ===

@app.get("/api/sessions")
async def list_sessions():
    """List all sessions."""
    sessions = read_sessions()
    return {
        "sessions": [s.to_dict() for s in sessions],
        "total": len(sessions),
        "waiting": len([s for s in sessions if s.status == "waiting"]),
        "running": len([s for s in sessions if s.status == "running"]),
    }


@app.get("/api/sessions/waiting")
async def list_waiting_sessions():
    """List sessions that are waiting for user input."""
    sessions = read_sessions()
    waiting = [s for s in sessions if s.status == "waiting"]
    return {
        "sessions": [s.to_dict() for s in waiting],
        "count": len(waiting),
    }


@app.get("/api/sessions/active")
async def list_active_sessions():
    """List sessions that are running or waiting."""
    sessions = read_sessions()
    active = [s for s in sessions if s.status in ("waiting", "running")]
    return {
        "sessions": [s.to_dict() for s in active],
        "count": len(active),
    }


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """Get details about a specific session."""
    session = get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Check tmux status
    tmux_exists = check_tmux_session(session.tmux_name)

    return {
        **session.to_dict(),
        "tmux_exists": tmux_exists,
    }


@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages_api(session_id: str, count: int = 10):
    """Get last messages from a session's Claude conversation."""
    session = get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = get_session_messages(session.project_path, count)
    session_file = get_claude_session_file(session.project_path)

    return {
        "session_id": session_id,
        "messages": messages,
        "count": len(messages),
        "session_file": str(session_file) if session_file else None,
    }


@app.get("/api/sessions/{session_id}/summary")
async def get_session_summary(session_id: str):
    """Get a summary of what's happening in a session."""
    session = get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get last messages for context
    messages = get_session_messages(session.project_path, 5)

    # Build simple summary
    summary = f"Session '{session.title}' is {session.status}."
    if messages:
        last_msg = messages[-1]
        if last_msg["role"] == "user":
            summary += f" Last user message: {last_msg['content'][:100]}..."
        elif last_msg["role"] == "assistant":
            if last_msg.get("tools"):
                summary += f" Agent is using tools: {', '.join(last_msg['tools'])}."
            elif last_msg.get("content"):
                summary += f" Agent said: {last_msg['content'][:100]}..."

    return {
        "session": session.to_dict(),
        "summary": summary,
        "last_messages": messages,
    }


@app.get("/api/sessions/{session_id}/waiting-for")
async def get_session_waiting_for(session_id: str):
    """Get what a session is waiting for (permission/question)."""
    session = get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status != "waiting":
        return {"waiting_for": None, "type": None}

    session_file = get_claude_session_file(session.project_path)
    if not session_file:
        return {"waiting_for": None, "type": None}

    # Read last entries to find what it's waiting for
    try:
        with open(session_file, 'r') as f:
            lines = f.readlines()[-30:]

        for line in reversed(lines):
            try:
                msg = json.loads(line)
                if msg.get("type") != "assistant":
                    continue

                content = msg.get("message", {}).get("content", [])
                for block in content:
                    if not isinstance(block, dict):
                        continue

                    # Check for tool permission
                    if block.get("type") == "tool_use":
                        tool_name = block.get("name", "")
                        tool_input = block.get("input", {})

                        description = ""
                        if tool_name == "Bash":
                            description = tool_input.get("command", "")[:80]
                        elif tool_name == "WebFetch":
                            description = tool_input.get("url", "")[:80]
                        elif tool_name == "WebSearch":
                            description = tool_input.get("query", "")[:80]
                        elif tool_name in ("Read", "Write", "Edit"):
                            description = tool_input.get("file_path", "")[:80]
                        elif tool_name == "Grep":
                            description = tool_input.get("pattern", "")[:80]

                        return {
                            "waiting_for": f"{tool_name}: {description}",
                            "type": "permission",
                            "tool": tool_name,
                            "description": description,
                        }

                    # Check for AskUserQuestion
                    if block.get("type") == "tool_use" and "AskUser" in block.get("name", ""):
                        questions = block.get("input", {}).get("questions", [])
                        if questions:
                            q = questions[0]
                            return {
                                "waiting_for": q.get("question", "")[:80],
                                "type": "question",
                                "question": q.get("question", ""),
                                "options": q.get("options", []),
                            }

            except:
                continue

    except:
        pass

    return {"waiting_for": None, "type": None}


def is_orchestrator_session(session: Session) -> bool:
    """Check if a session can be an orchestrator (has voice capability)."""
    # Sessions with "Voice" or "Orchestrator" in title or group
    title_lower = session.title.lower()
    group_lower = session.group_path.lower()
    return (
        "voice" in title_lower or
        "orchestrator" in title_lower or
        "watcher" in title_lower or
        "voice" in group_lower or
        "orchestrator" in group_lower
    )


def get_session_room_name(session: Session) -> str:
    """Get LiveKit room name for a session."""
    return f"voicemode-{session.id[:8]}"


@app.get("/api/orchestrators")
async def list_orchestrators():
    """List sessions that can be orchestrators (voice-enabled)."""
    sessions = read_sessions()
    orchestrators = [s for s in sessions if is_orchestrator_session(s)]

    return {
        "orchestrators": [
            {
                **s.to_dict(),
                "room_name": get_session_room_name(s),
            }
            for s in orchestrators
        ],
        "count": len(orchestrators),
    }


@app.get("/api/livekit/token")
async def get_livekit_token(room: str = None, session_id: str = None):
    """Generate LiveKit token for client.

    Args:
        room: Optional room name (defaults to main voicemode room)
        session_id: Optional session ID to connect to that session's room
    """
    try:
        from livekit import api
    except ImportError:
        raise HTTPException(status_code=500, detail="LiveKit SDK not installed")

    # Determine room name
    if session_id:
        session = get_session_by_id(session_id)
        if session:
            room = get_session_room_name(session)
    if not room:
        room = LIVEKIT_ROOM  # Default room

    token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    token.with_identity("iphone-user")
    token.with_name("iPhone")
    token.with_ttl(timedelta(hours=24))
    token.with_grants(api.VideoGrants(
        room_join=True,
        room=room,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True
    ))

    return {
        "token": token.to_jwt(),
        "url": LIVEKIT_WS_URL,
        "room": room,
    }


@app.get("/api/config")
async def get_config():
    """Get client configuration."""
    return {
        "livekit_url": LIVEKIT_WS_URL,
        "livekit_room": LIVEKIT_ROOM,
    }


@app.get("/")
async def index():
    """Serve the main page."""
    html_path = Path(__file__).parent / "voice_watcher.html"
    if html_path.exists():
        return FileResponse(html_path)
    return HTMLResponse("<h1>Voice Watcher</h1><p>HTML file not found</p>")


if __name__ == "__main__":
    print("=" * 50)
    print("Voice Watcher")
    print("=" * 50)
    print(f"Reading sessions from: {AOE_SESSIONS_FILE}")
    print(f"Sessions file exists: {AOE_SESSIONS_FILE.exists()}")

    sessions = read_sessions()
    waiting = [s for s in sessions if s.status == "waiting"]
    running = [s for s in sessions if s.status == "running"]

    print(f"\nFound {len(sessions)} sessions:")
    print(f"  - {len(waiting)} waiting")
    print(f"  - {len(running)} running")
    print(f"  - {len(sessions) - len(waiting) - len(running)} idle")

    if waiting:
        print("\n⏳ Waiting for input:")
        for s in waiting:
            print(f"   {s.title} ({s.group_path})")

    print("\n" + "=" * 50)
    print("Starting server on http://localhost:8890")
    print("=" * 50)

    uvicorn.run(app, host="0.0.0.0", port=8890)
