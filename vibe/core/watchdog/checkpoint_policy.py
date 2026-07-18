from __future__ import annotations

from enum import StrEnum, auto


class RestoreLocation(StrEnum):
    WATCHDOG_WORKTREE = auto()
    MAIN_CHECKOUT = auto()
    OUTSIDE_TRUSTED_ROOTS = auto()


class RestoreVerdict(StrEnum):
    ALLOW = auto()
    ASK = auto()
    DENY = auto()


class CheckpointRestorePolicy:
    def decide(
        self,
        *,
        location: RestoreLocation,
        ownership_proven: bool,
        manual_edit_after_checkpoint: bool,
        current_epoch: int,
        checkpoint_epoch: int,
    ) -> RestoreVerdict:
        if location in {
            RestoreLocation.MAIN_CHECKOUT,
            RestoreLocation.OUTSIDE_TRUSTED_ROOTS,
        }:
            return RestoreVerdict.DENY
        if current_epoch != checkpoint_epoch:
            return RestoreVerdict.DENY
        if not ownership_proven or manual_edit_after_checkpoint:
            return RestoreVerdict.ASK
        return RestoreVerdict.ALLOW
