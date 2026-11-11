from __future__ import annotations

from vibe.app_server._error_message_cap import (
    ERROR_MESSAGE_MAX_CHARS,
    ERROR_TRUNCATION_MARKER,
    bounded_error_message,
)

_PAYLOAD = "R0lGODlhsASwBPf/" * 70_000


def test_short_message_is_unchanged() -> None:
    message = "API error occurred: Status 400. Body: invalid request"

    assert bounded_error_message(message) == message


def test_echoed_data_uri_is_replaced_by_its_size() -> None:
    message = (
        "API error occurred: Status 400. Body: "
        f'{{"message":"Image is an animated GIF. Image URL: data:image/gif;base64,{_PAYLOAD}"}}'
    )

    bounded = bounded_error_message(message)

    assert bounded == (
        "API error occurred: Status 400. Body: "
        '{"message":"Image is an animated GIF. Image URL: '
        f'data:image/gif;base64,<{len(_PAYLOAD)} base64 chars omitted>"}}'
    )


def test_data_uri_with_parameters_keeps_its_header() -> None:
    message = f"bad image data:image/png;name=cat.png;base64,{_PAYLOAD}== end"

    assert bounded_error_message(message) == (
        f"bad image data:image/png;name=cat.png;base64,"
        f"<{len(_PAYLOAD) + 2} base64 chars omitted> end"
    )


def test_every_data_uri_is_replaced_and_plain_data_urls_are_kept() -> None:
    message = (
        f"a data:image/png;base64,{_PAYLOAD} b data:image/jpeg;base64,QUJD "
        "c data:text/plain,hi"
    )

    assert bounded_error_message(message) == (
        f"a data:image/png;base64,<{len(_PAYLOAD)} base64 chars omitted> "
        "b data:image/jpeg;base64,<4 base64 chars omitted> c data:text/plain,hi"
    )


def test_long_message_is_capped_with_a_marker() -> None:
    bounded = bounded_error_message("x" * (ERROR_MESSAGE_MAX_CHARS * 50))

    assert bounded == f"{'x' * ERROR_MESSAGE_MAX_CHARS}{ERROR_TRUNCATION_MARKER}"


def test_capping_is_idempotent() -> None:
    bounded = bounded_error_message("x" * (ERROR_MESSAGE_MAX_CHARS * 50))

    assert bounded_error_message(bounded) == bounded
