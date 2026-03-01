from __future__ import annotations

import argparse
from pathlib import Path

from vibe.game_tutor.orchestrator import MistralVibeOrchestrator


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate tutorials and strategy UI from rules text.")
    parser.add_argument("rules", type=Path, help="Path to raw rules file")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("game-tutor"),
        help="Output directory for generated artifacts",
    )
    args = parser.parse_args()

    manifest = MistralVibeOrchestrator().run(args.rules, args.output)
    print(f"Generated artifacts for {manifest['parsed_rules']['game_name']} at {args.output}")


if __name__ == "__main__":
    main()
