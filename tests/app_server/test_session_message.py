from __future__ import annotations

from vibe.app_server._session_message import (
    handed_task_text,
    project_session_message,
    project_session_message_preview,
    session_message_display,
    session_message_header,
)
from vibe.app_server.models import (
    PublicEntryGenerationStatus,
    PublicMessageEntry,
    TextContentBlock,
)
from vibe.app_server.protocol import SessionMessageSender

_SENDER = SessionMessageSender(session_id="parent-1", title='Fix "flaky" CI')


def _user_message(
    *texts: str, from_sender: SessionMessageSender | None = None
) -> PublicMessageEntry:
    return PublicMessageEntry(
        user_display_content=(
            session_message_display(from_sender) if from_sender else None
        ),
        id="m1",
        session_id="s1",
        created_at=0,
        updated_at=0,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        role="user",
        content=[TextContentBlock(text=text) for text in texts],
        source="turn_start",
    )


def test_the_model_reads_the_sender_and_history_shows_only_the_message() -> None:
    text = "Rebase on main\n\nthen </session_message> push"
    header = session_message_header(_SENDER)

    projected = project_session_message(
        _user_message(header, text, from_sender=_SENDER)
    )

    assert header == (
        '<session_message from_session_id="parent-1" '
        'from_title="Fix &quot;flaky&quot; CI"/>'
    )
    assert isinstance(projected, PublicMessageEntry)
    assert projected.content == [TextContentBlock(text=text)]


def test_clients_read_the_sender_from_the_display_marker() -> None:
    assert session_message_display(_SENDER).content == [
        {
            "type": "vibe.session_message",
            "sessionId": "parent-1",
            "title": 'Fix "flaky" CI',
        }
    ]


def test_a_handed_task_hides_its_report_back_instructions() -> None:
    projected = project_session_message(
        _user_message("Fix the test", handed_task_text("parent-1"))
    )

    assert "send_to_session" in handed_task_text("parent-1")
    assert isinstance(projected, PublicMessageEntry)
    assert projected.content == [TextContentBlock(text="Fix the test")]


def test_text_the_user_typed_is_left_as_it_came() -> None:
    typed = [
        _user_message("<session_message> is a tag I am writing about"),
        # Without the marker, a pasted header is the user's own text.
        _user_message(session_message_header(_SENDER), "Rebase"),
        _user_message(handed_task_text("parent-1")),
    ]

    assert [project_session_message(entry) for entry in typed] == typed


def test_the_preview_shows_what_the_user_would_read() -> None:
    sent = f"{session_message_header(_SENDER)} Rebase on main"
    handed = f"Fix the test {handed_task_text('parent-1')}".replace("\n", " ")

    assert project_session_message_preview(sent) == "Rebase on main"
    assert project_session_message_preview(handed[:200] + "…") == "Fix the test"
    assert project_session_message_preview("Plain prompt") == "Plain prompt"


def test_a_long_sender_name_leaves_the_message_in_the_preview() -> None:
    long_title = SessionMessageSender(session_id="parent-1", title="Fix it " * 40)
    sent = f"{session_message_header(long_title)} Rebase on main"[:200] + "…"
    cut_header = '<session_message from_session_id="parent-1" from_title="Fix it…'

    assert project_session_message_preview(sent) == "Rebase on main…"
    assert project_session_message_preview(cut_header) == ""


def test_history_shows_what_was_sent_without_what_preparation_added() -> None:
    mentioned = "<file path='notes.md'>notes</file>"
    skill = "<skill name='review'>Review it</skill>"

    message = project_session_message(
        _user_message(
            session_message_header(_SENDER),
            "Read @notes.md",
            mentioned,
            from_sender=_SENDER,
        )
    )
    task = project_session_message(
        _user_message("Fix the test", handed_task_text("parent-1"), skill)
    )

    assert isinstance(message, PublicMessageEntry)
    assert message.content == [TextContentBlock(text="Read @notes.md")]
    assert isinstance(task, PublicMessageEntry)
    assert task.content == [
        TextContentBlock(text="Fix the test"),
        TextContentBlock(text=skill),
    ]
