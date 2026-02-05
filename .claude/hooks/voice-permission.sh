#!/bin/bash
# Voice permission hook for Claude Code
# Uses TTS to ask question, then reads response from voice_loop transcriptions

set -e

cd /Users/maciej/work/drads/voice-mode/voice-mode

# Lock file to tell voice_loop not to send to tmux
LOCK_FILE="/tmp/voice-permission-active.lock"

# Cleanup function
cleanup() {
    rm -f "$LOCK_FILE"
}
trap cleanup EXIT

# Create lock file
touch "$LOCK_FILE"

# Source voicemode env
source ~/.voicemode/voicemode.env 2>/dev/null || true

# Read JSON from stdin
INPUT=$(cat)

# Log input for debugging
echo "$(date): New permission request" >> /tmp/voice-permission-hook.log
echo "$INPUT" >> /tmp/voice-permission-hook.log

# Extract tool name only - keep it short!
TOOL_NAME=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_name', 'unknown'))" 2>/dev/null || echo "unknown")

# Create SHORT speech message - just tool name, no details
MESSAGE="Permission for ${TOOL_NAME}. Yes or no?"

echo "Asking: $MESSAGE" >> /tmp/voice-permission-hook.log

# Mark current position in voice_loop log
LOGFILE=/tmp/voice_loop.log
if [ -f "$LOGFILE" ]; then
    STARTLINE=$(wc -l < "$LOGFILE")
else
    STARTLINE=0
fi

# Speak the question via TTS (don't wait for response in voicemode)
uv run voicemode converse -m "$MESSAGE" --no-wait >/dev/null 2>&1

# Wait for user response via voice_loop (check log for new transcriptions)
echo "Waiting for voice response..." >> /tmp/voice-permission-hook.log

for i in $(seq 1 30); do
    sleep 1

    if [ -f "$LOGFILE" ]; then
        # Get new lines since we started
        NEWLINES=$(tail -n +$((STARTLINE + 1)) "$LOGFILE" 2>/dev/null | grep '"type": "transcription"' | tail -3)

        if [ -n "$NEWLINES" ]; then
            # Extract text from JSON
            HEARD=$(echo "$NEWLINES" | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if line:
        try:
            d = json.loads(line)
            print(d.get('text', ''))
        except:
            pass
" 2>/dev/null | tail -1)

            echo "Heard: $HEARD" >> /tmp/voice-permission-hook.log

            if echo "$HEARD" | grep -iqE "\b(yes|yeah|yep|tak|okay|ok|sure|approve|go|dawaj|dobra|jasne|pewnie)\b"; then
                echo "Decision: ALLOW" >> /tmp/voice-permission-hook.log
                echo '{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}'
                exit 0
            elif echo "$HEARD" | grep -iqE "\b(no|nope|nie|deny|stop|cancel|reject)\b"; then
                echo "Decision: DENY" >> /tmp/voice-permission-hook.log
                echo '{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"deny","message":"Denied via voice"}}}'
                exit 0
            fi
        fi
    fi
done

# Timeout - fall back to manual
echo "Decision: MANUAL (timeout)" >> /tmp/voice-permission-hook.log
exit 1
