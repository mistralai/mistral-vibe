from __future__ import annotations

from vibe.cli import cli as cli_mod
from vibe.core.config import harness_files
from vibe.core.paths import HISTORY_FILE


def test_bootstrap_does_not_create_config_file() -> None:
    config_file = harness_files.get_harness_files_manager().user_config_file
    config_file.unlink(missing_ok=True)

    cli_mod.bootstrap_config_files()

    # Defaults come from DefaultConfigLayer at merge time; bootstrap no longer
    # seeds a config.toml — it is created on the first persisted change.
    assert not config_file.exists()


def test_bootstrap_seeds_history_greeting() -> None:
    history_file = HISTORY_FILE.path
    history_file.unlink(missing_ok=True)

    cli_mod.bootstrap_config_files()

    assert history_file.exists()
    assert history_file.read_text(encoding="utf-8") == "Hello Vibe!\n"
