from __future__ import annotations

from pydantic import ValidationError
import pytest

from vibe.core.watchdog import TiltEvaluation, TiltScorePolicy, parse_tilt_evaluation


def test_tilt_score_policy_has_deterministic_threshold() -> None:
    policy = TiltScorePolicy(recovery_threshold=80)

    assert not policy.authorizes_context_injection(TiltEvaluation(score=79))
    assert policy.authorizes_context_injection(TiltEvaluation(score=80))


def test_tilt_evaluation_parser_accepts_json_or_json_fence() -> None:
    assert parse_tilt_evaluation('{"score": 91}').score == 91
    assert parse_tilt_evaluation('```json\n{"score": 91}\n```').score == 91


def test_tilt_evaluation_parser_rejects_prose_and_out_of_range_score() -> None:
    with pytest.raises(ValidationError):
        parse_tilt_evaluation("The score is 91 because the agent is stuck.")
    with pytest.raises(ValidationError):
        parse_tilt_evaluation('{"score": 101}')
