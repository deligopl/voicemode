"""
Session monitoring for Claude Code / Agent of Empires.

Provides read-only access to session information and history.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Paths
AOE_SESSIONS_FILE = Path.home() / ".agent-of-empires" / "profiles" / "default" / "sessions.json"
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


@dataclass
class Session:
    """A Claude Code session from Agent of Empires."""
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
        safe_title = re.sub(r'[^a-zA-Z0-9_]', '', self.title.replace(' ', '_'))
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

    @property
    def claude_dir(self) -> Path:
        """Get the Claude projects directory for this session."""
        # /Users/foo/bar -> -Users-foo-bar
        dir_name = self.project_path.replace("/", "-")
        return CLAUDE_PROJECTS_DIR / dir_name


def list_sessions() -> list[Session]:
    """List all sessions from AoE config (READ-ONLY)."""
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
        return []


def find_session(name: str) -> Optional[Session]:
    """Find session by title (partial match, case-insensitive)."""
    sessions = list_sessions()
    name_lower = name.lower()

    # Exact match first
    for s in sessions:
        if s.title.lower() == name_lower:
            return s

    # Partial match
    for s in sessions:
        if name_lower in s.title.lower():
            return s

    return None


def get_session_file(session: Session) -> Optional[Path]:
    """Get the most recent JSONL file for a session."""
    claude_dir = session.claude_dir
    if not claude_dir.exists():
        return None

    jsonl_files = list(claude_dir.glob("*.jsonl"))
    if not jsonl_files:
        return None

    return max(jsonl_files, key=lambda p: p.stat().st_mtime)


def get_all_session_files(session: Session) -> list[Path]:
    """Get all JSONL files for a session's project, sorted by mtime (newest first)."""
    claude_dir = session.claude_dir
    if not claude_dir.exists():
        return []

    jsonl_files = list(claude_dir.glob("*.jsonl"))
    return sorted(jsonl_files, key=lambda p: p.stat().st_mtime, reverse=True)


def get_session_file_summary(session_file: Path) -> dict:
    """Get a quick summary of a session file (last message, size, etc.)."""
    import os
    from datetime import datetime

    stat = session_file.stat()
    size_mb = stat.st_size / (1024 * 1024)
    mtime = datetime.fromtimestamp(stat.st_mtime)

    # Get last meaningful message
    last_text = None
    last_tools = []

    try:
        # Read last 50 lines to find a message
        with open(session_file, 'r') as f:
            lines = f.readlines()[-50:]

        for line in reversed(lines):
            try:
                msg = json.loads(line)
                if msg.get("type") == "assistant":
                    content = msg.get("message", {}).get("content", [])
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                text = block.get("text", "")[:100]
                                if text and not text.startswith("["):
                                    last_text = text
                                    break
                            elif block.get("type") == "tool_use":
                                last_tools.append(block.get("name"))
                    if last_text or last_tools:
                        break
            except:
                continue
    except:
        pass

    return {
        "file": session_file.name,
        "size_mb": round(size_mb, 1),
        "modified": mtime.strftime("%H:%M"),
        "last_text": last_text,
        "last_tools": last_tools,
    }


def read_session_messages(session: Session, count: int = 20) -> list[dict]:
    """Read last N messages from a session's history."""
    session_file = get_session_file(session)
    if not session_file:
        return []

    messages = []
    try:
        with open(session_file, 'r') as f:
            for line in f:
                try:
                    msg = json.loads(line.strip())
                    if msg.get("type") in ("user", "assistant"):
                        formatted = format_message(msg)
                        if formatted:
                            messages.append(formatted)
                except:
                    continue

        return messages[-count:]
    except Exception:
        return []


def format_message(msg: dict) -> Optional[dict]:
    """Format a JSONL message for display."""
    msg_type = msg.get("type")
    content = msg.get("message", {}).get("content", [])

    text = ""
    tools = []

    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                block_text = block.get("text", "")
                # Skip system messages
                if block_text.startswith("[Request interrupted"):
                    continue
                text += block_text
            elif block.get("type") == "tool_use":
                tools.append(block.get("name", "?"))
            elif block.get("type") == "tool_result":
                result = block.get("content", "")
                if isinstance(result, str) and result:
                    text = f"[Output: {result[:100]}...]" if len(result) > 100 else f"[Output: {result}]"

    if not text and not tools:
        return None

    return {
        "role": msg_type,
        "text": text[:300] if text else None,
        "tools": tools if tools else None,
        "timestamp": msg.get("timestamp"),
    }


def get_waiting_sessions() -> list[Session]:
    """Get sessions waiting for user input."""
    return [s for s in list_sessions() if s.status == "waiting"]


def get_running_sessions() -> list[Session]:
    """Get currently running sessions."""
    return [s for s in list_sessions() if s.status == "running"]


def get_active_sessions() -> list[Session]:
    """Get sessions that are running or waiting."""
    return [s for s in list_sessions() if s.status in ("waiting", "running")]


def send_to_session(session: Session, text: str, press_enter: bool = True) -> bool:
    """Send text to a session's tmux."""
    import subprocess

    tmux_name = session.tmux_name
    cmd = ["tmux", "send-keys", "-t", tmux_name, text]
    if press_enter:
        cmd.append("Enter")

    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError:
        return False


def send_confirmation(session: Session, confirm: bool = True) -> bool:
    """Send yes/no confirmation to a waiting session.

    For Claude Code permission prompts, just pressing Enter confirms option 1 (Yes).
    Sending 'n' or pressing down+Enter would deny.
    """
    if confirm:
        # Just press Enter to confirm (option 1 is pre-selected)
        return send_to_session(session, "", press_enter=True)
    else:
        # Send 'n' for no
        return send_to_session(session, "n", press_enter=True)


def get_pending_question(session: Session) -> Optional[dict]:
    """Get the last unanswered AskUserQuestion from session history."""
    session_file = get_session_file(session)
    if not session_file:
        return None

    try:
        with open(session_file, 'r') as f:
            lines = f.readlines()

        # Search backwards for last AskUserQuestion
        last_question = None
        last_question_answered = False

        for line in reversed(lines[-100:]):  # Check last 100 lines
            try:
                msg = json.loads(line)
                msg_type = msg.get("type")

                # If we find a user response after finding a question, it's answered
                if last_question and msg_type == "user":
                    content = msg.get("message", {}).get("content", [])
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            # Found answer to the question
                            last_question_answered = True
                            break

                # Look for AskUserQuestion tool use
                if msg_type == "assistant":
                    content = msg.get("message", {}).get("content", [])
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            if "AskUser" in block.get("name", ""):
                                questions = block.get("input", {}).get("questions", [])
                                if questions:
                                    last_question = {
                                        "question": questions[0].get("question", ""),
                                        "header": questions[0].get("header", ""),
                                        "options": questions[0].get("options", []),
                                    }
                                    if not last_question_answered:
                                        return last_question
                                    # Keep searching for unanswered question
                                    last_question = None
                                    last_question_answered = False

            except:
                continue

        return None
    except:
        return None


def answer_question(session: Session, option_index: int) -> bool:
    """Answer a pending question by selecting option (1-based index)."""
    # Claude Code accepts the option number or label
    return send_to_session(session, str(option_index), press_enter=True)


def get_pending_permission(session: Session) -> Optional[dict]:
    """Get pending tool permission request (yes/no prompt)."""
    session_file = get_session_file(session)
    if not session_file:
        return None

    try:
        with open(session_file, 'r') as f:
            lines = f.readlines()

        # Check last few lines for tool_use without tool_result
        last_tool_use = None

        for line in reversed(lines[-20:]):
            try:
                msg = json.loads(line)
                msg_type = msg.get("type")

                # If we find a tool_result, the permission was already granted
                if msg_type == "user":
                    content = msg.get("message", {}).get("content", [])
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            return None  # Already answered

                # Look for tool_use
                if msg_type == "assistant":
                    content = msg.get("message", {}).get("content", [])
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tool_name = block.get("name", "")
                            tool_input = block.get("input", {})

                            # Extract useful info based on tool type
                            description = ""
                            if tool_name == "Bash":
                                description = tool_input.get("command", "")[:100]
                            elif tool_name == "WebFetch":
                                description = tool_input.get("url", "")[:100]
                            elif tool_name == "WebSearch":
                                description = tool_input.get("query", "")[:100]
                            elif tool_name in ("Read", "Write", "Edit"):
                                description = tool_input.get("file_path", "")[:100]
                            else:
                                description = str(tool_input)[:100]

                            return {
                                "tool": tool_name,
                                "description": description,
                            }
            except:
                continue

        return None
    except:
        return None
