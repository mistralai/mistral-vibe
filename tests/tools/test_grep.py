from __future__ import annotations

from pathlib import Path

from vibe.core.tools.builtins.grep import GrepResult


def test_cwd_is_not_serialized_into_the_model_facing_result():
    result = GrepResult(
        matches="target.py:1:NEEDLE",
        match_count=1,
        pattern="NEEDLE",
        was_truncated=False,
        cwd="/private/workspace",
    )

    dumped = result.model_dump(mode="json")
    result_text = "\n".join(f"{key}: {value}" for key, value in dumped.items())

    assert "cwd" not in dumped
    assert "/private/workspace" not in result_text
    assert result.parsed_matches[0].path == str(
        (Path("/private/workspace") / "target.py").resolve()
    )
