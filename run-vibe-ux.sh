#!/usr/bin/env bash
# Lance mistral-vibe avec l'agent Design (UI/UX)
# Utilise --agent design pour activer le plugin design unifié
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
exec uv run vibe --agent design "$@"
