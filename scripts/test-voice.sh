#!/usr/bin/env bash
# Ten-second microphone check: hear the wake word, transcribe, speak it back.
# Deliberately does not involve the brain - it isolates the audio stack.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
cd "$REPO_ROOT"
exec uv run python -m jervis_voice.smoke "${1:-10}"
