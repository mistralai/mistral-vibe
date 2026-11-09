from __future__ import annotations

from vibe.core.utils.exceptions import first_of_type, leaf_exceptions


def test_leaf_exceptions_flattens_nested_groups() -> None:
    a, b = ValueError("a"), RuntimeError("b")
    exc = ExceptionGroup("o", [ExceptionGroup("i", [a]), b])
    assert leaf_exceptions(exc) == [a, b]


def test_first_of_type_finds_nested_match() -> None:
    target = ValueError("x")
    exc = ExceptionGroup("o", [RuntimeError("r"), ExceptionGroup("i", [target])])
    assert first_of_type(exc, ValueError) is target
    assert first_of_type(exc, KeyError) is None
