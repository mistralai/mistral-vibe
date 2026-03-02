#!/usr/bin/env bash
# Launch mistral-vibe with the Design (UI/UX) agent
# Utilise --agent design pour activer le plugin design unifié
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
exec uv run vibemic --agent design "$@"
