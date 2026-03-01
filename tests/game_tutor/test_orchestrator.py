from __future__ import annotations

from pathlib import Path

from vibe.game_tutor.orchestrator import MistralVibeOrchestrator


def test_orchestrator_generates_artifacts(tmp_path: Path) -> None:
    rules = tmp_path / "chess_rules.txt"
    rules.write_text(
        "The king moves one square in any direction. You win by checkmating the enemy king.",
        encoding="utf-8",
    )

    output = tmp_path / "out"
    manifest = MistralVibeOrchestrator().run(rules, output)

    assert manifest["parsed_rules"]["game_name"] == "Chess"
    assert (output / "tutorials" / "chess.json").exists()
    assert (output / "strategies" / "chess.json").exists()
    assert (output / "GameTutorApp.tsx").exists()
    assert (output / "manifest.json").exists()


def test_orchestrator_mahjong_detection(tmp_path: Path) -> None:
    rules = tmp_path / "custom.txt"
    rules.write_text("Players may call pon and chii before declaring riichi.", encoding="utf-8")

    output = tmp_path / "out"
    manifest = MistralVibeOrchestrator().run(rules, output)

    assert manifest["parsed_rules"]["game_name"] == "Riichi Mahjong"
