from __future__ import annotations

from enum import StrEnum, auto

from vibe.core.watchdog.models import RecoveryStrategy


class AuthorizationVerdict(StrEnum):
    ALLOW = auto()
    ASK = auto()
    DENY = auto()


class RecoveryAuthorizationPolicy:
    def authorize(self, strategy: RecoveryStrategy) -> AuthorizationVerdict:
        match strategy:
            case RecoveryStrategy.INJECT_CONTEXT:
                return AuthorizationVerdict.ALLOW
            case RecoveryStrategy.CANCEL_AND_CONTINUE:
                return AuthorizationVerdict.ASK
            case RecoveryStrategy.RESTORE_CHECKPOINT:
                return AuthorizationVerdict.ASK
            case RecoveryStrategy.RESTART_PROCESS:
                return AuthorizationVerdict.ASK
            case RecoveryStrategy.ASK_USER:
                return AuthorizationVerdict.ALLOW
            case RecoveryStrategy.STOP:
                return AuthorizationVerdict.ALLOW
