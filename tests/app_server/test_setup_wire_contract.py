"""The Python side of the ``setup_wire.json`` pin.

Rust round-trips this fixture in ``vibe/cli-rust/tests/units/setup_wire.rs``;
these tests pin the same frames against the Python protocol models, so the
contract can never re-diverge from one side alone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server.protocol import (
    SetupStatusResponse,
    SetupStoreCredentialParams,
    SetupSubmitChoicesParams,
)

_FIXTURE = (
    Path(__file__)
    .resolve()
    .parents[2]
    .joinpath("vibe", "cli-rust", "tests", "onboarding_common", "setup_wire.json")
)


def _frame(name: str) -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))[name]


def _round_trips(model: type[ProtocolModel], frame: dict[str, Any]) -> None:
    parsed = validate_wire(model, frame)
    assert parsed.model_dump(mode="json", by_alias=True) == frame


def test_status_response_round_trips_the_fixture() -> None:
    _round_trips(SetupStatusResponse, _frame("statusResponse"))


def test_store_credential_params_round_trip_the_fixture() -> None:
    _round_trips(SetupStoreCredentialParams, _frame("storeCredentialParams"))


def test_submit_choices_params_round_trip_the_fixture() -> None:
    _round_trips(SetupSubmitChoicesParams, _frame("submitChoicesParams"))
