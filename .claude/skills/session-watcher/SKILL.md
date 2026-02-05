---
name: session-watcher
description: Monitor other Claude Code sessions. Use when user asks about sessions, what agents are doing, session status, or to check on other running agents.
---

# Session Watcher

Monitor Claude Code sessions running in Agent of Empires.

## Commands

```bash
# List all sessions
voicemode sessions list

# Show only waiting/running/idle
voicemode sessions list --status waiting

# Sessions waiting for input
voicemode sessions waiting
voicemode sessions waiting -d    # with details (what they're waiting for)

# Active sessions (running + waiting)
voicemode sessions active

# Details about a session (partial name match)
voicemode sessions info "GreenRoom"

# Last messages from a session
voicemode sessions history "GreenRoom"
voicemode sessions history "GreenRoom" -n 20

# Send text to a session (via tmux)
voicemode sessions send "Deligo" "tak, kontynuuj"

# Send confirmation (yes/no)
voicemode sessions confirm "GreenRoom"       # sends "yes" + Enter
voicemode sessions confirm "GreenRoom" --no  # sends "no" + Enter

# Show pending question with options
voicemode sessions question "Deligo"

# Answer a question (by option number)
voicemode sessions answer "Deligo" 1         # wybiera opcję 1
voicemode sessions answer "Deligo" 2         # wybiera opcję 2

# Show pending tool permission (yes/no)
voicemode sessions permission "Garmin"       # shows what tool needs permission
```

## Voice Usage

When user asks about sessions via voice:

1. Run the appropriate command to get info
2. Summarize the results concisely
3. Respond via `voicemode:converse`

**Checking status:**
- "Które sesje czekają?" → `voicemode sessions waiting`
- "Co robi GreenRoom?" → `voicemode sessions history GreenRoom`
- "Ile mam aktywnych agentów?" → `voicemode sessions active`

**Interacting with sessions:**
- "Potwierdź w Deligo" → `voicemode sessions confirm Deligo`
- "Wyślij nie do GreenRoom" → `voicemode sessions confirm GreenRoom --no`
- "Napisz do Deligo: kontynuuj" → `voicemode sessions send Deligo "kontynuuj"`
- "Na co czeka Garmin?" → `voicemode sessions permission Garmin`
- "Co czeka i na co?" → `voicemode sessions waiting -d`

## Notes

- This is READ-ONLY - we don't modify sessions
- Sessions are managed by Agent of Empires (AoE)
- To connect to a session: `tmux attach -t <tmux_name>`
