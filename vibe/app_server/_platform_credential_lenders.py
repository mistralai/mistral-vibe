"""Desktop channels that lend an account's sign-in to every session of it.

A session keeps running after the chat channel that started it closes, and
a session resumed by the server has no chat channel at all. Desktop is still
running and signed in, on another channel of the same process. So each
Desktop channel that lends its sign-in registers here under the account it
reported at ``initialize``, and a session whose own channel is gone borrows
from a live channel of the same account. Another account's channel never
lends: its tokens would run the session as someone else.

A channel declares at ``initialize`` whether it lives as long as the process
(Desktop's catalogue) or as long as its chat: ``availability`` tells whether
an account can count on a lender, so the server decides what to resume.

The account is the one a channel reported, which can go stale (Desktop
switched account) or be wrong. So a borrowed ID token is checked before a
session uses it, and refused unless both hold:

- the organization the lender named with it is the account's;
- its user is the one the session first ran as. A session that has not run
  yet takes the first borrowed user that passes the organization check.

A session's own channel is not checked: it is the client that runs it.
A lender whose token names another organization stops lending to that
account until it signs in again; on another user's token, only that session
skips it. Either way the next lender is tried.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal

from vibe.app_server._connection_protocol import ClientAccount
from vibe.app_server._platform_credentials import (
    ConnectionPlatformCredentialProvider,
    normalized_uuid,
)
from vibe.core.platform_auth import (
    PlatformCredentialIdentity,
    PlatformCredentialRequired,
    PlatformCredentialResult,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)
from vibe.observability.logging import logger

# ``ClientAccount`` is a mutable model, so not hashable.
type AccountKey = tuple[str, str, str]


def account_key(account: ClientAccount) -> AccountKey:
    return (account.organization_id, account.workspace_id, account.user_id)


type LenderAvailability = Literal["durable", "connections_only", "none"]


@dataclass(slots=True)
class _Lender:
    provider: ConnectionPlatformCredentialProvider
    durable: bool
    # Set when one of its tokens failed the account check: for every session
    # (another organization) or for those of some users (another user).
    # Cleared when it signs in again.
    refused: bool = field(default=False)
    foreign_to: set[str] = field(default_factory=set)

    def lends_to(self, user: str | None) -> bool:
        return not self.refused and (user is None or user not in self.foreign_to)


class PlatformCredentialLenders:
    def __init__(self) -> None:
        self._lenders: dict[AccountKey, list[_Lender]] = {}
        self._waiters: dict[AccountKey, set[asyncio.Future[None]]] = {}

    def changed(self, account: AccountKey) -> asyncio.Future[None]:
        """A future done at the next change that can move ``account``'s
        availability: a lender registered, unregistered, refused or
        reinstated. Take it before checking ``availability``, so a change in
        between is not missed; cancel it when no longer waiting.
        """
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(account, set()).add(waiter)
        waiter.add_done_callback(lambda done: self._forget(account, done))
        return waiter

    def _forget(self, account: AccountKey, waiter: asyncio.Future[None]) -> None:
        waiters = self._waiters.get(account)
        if waiters is None:
            return
        waiters.discard(waiter)
        if not waiters:
            del self._waiters[account]

    def _changed(self, account: AccountKey) -> None:
        for waiter in list(self._waiters.get(account, ())):
            if not waiter.done():
                waiter.set_result(None)

    def register(
        self,
        account: AccountKey,
        lender: ConnectionPlatformCredentialProvider,
        *,
        durable: bool = False,
    ) -> None:
        self.unregister(lender)
        self._lenders.setdefault(account, []).append(_Lender(lender, durable))
        self._changed(account)

    def unregister(self, lender: ConnectionPlatformCredentialProvider) -> None:
        for account, lenders in list(self._lenders.items()):
            kept = [entry for entry in lenders if entry.provider is not lender]
            if len(kept) == len(lenders):
                continue
            if kept:
                lenders[:] = kept
            else:
                del self._lenders[account]
            self._changed(account)

    def is_live(self, lender: ConnectionPlatformCredentialProvider) -> bool:
        return any(
            entry.provider is lender
            for lenders in self._lenders.values()
            for entry in lenders
        )

    def _usable(self, account: AccountKey, user: str | None) -> list[_Lender]:
        return [e for e in self._lenders.get(account, []) if e.lends_to(user)]

    def lenders_for(
        self, account: AccountKey, user: str | None = None
    ) -> list[ConnectionPlatformCredentialProvider]:
        """The lenders a session of ``account`` running as ``user`` may ask,
        most preferred first: durable ones, then the newest.
        """
        usable = self._usable(account, user)
        ordered = sorted(reversed(usable), key=lambda entry: not entry.durable)
        return [entry.provider for entry in ordered]

    def _entries(
        self, lender: ConnectionPlatformCredentialProvider
    ) -> list[tuple[AccountKey, _Lender]]:
        return [
            (account, entry)
            for account, lenders in self._lenders.items()
            for entry in lenders
            if entry.provider is lender
        ]

    def refuse(
        self, lender: ConnectionPlatformCredentialProvider, *, user: str | None = None
    ) -> None:
        """Stop ``lender`` lending: to sessions running as ``user``, or to
        every session when no user is given.
        """
        for account, entry in self._entries(lender):
            if entry.refused or (user is not None and user in entry.foreign_to):
                continue
            if user is None:
                entry.refused = True
            else:
                entry.foreign_to.add(user)
            self._changed(account)

    def reinstate(self, lender: ConnectionPlatformCredentialProvider) -> None:
        for account, entry in self._entries(lender):
            if entry.refused or entry.foreign_to:
                entry.refused = False
                entry.foreign_to.clear()
                self._changed(account)

    def availability(
        self, account: AccountKey, user: str | None = None
    ) -> LenderAvailability:
        """Whether a session of ``account`` running as ``user``, with no
        channel of its own, can get a credential: ``durable`` while a
        process-lifetime channel lends to it, ``connections_only`` while only
        chats do, else ``none``.
        """
        usable = self._usable(account, user)
        if not usable:
            return "none"
        return "durable" if any(e.durable for e in usable) else "connections_only"


class AccountPlatformCredentialProvider:
    """A Desktop connection's credential: its own channel while it is open,
    then a live channel of the same account, else ``transient`` so the turn
    waits for one within the harness's credential budget.
    """

    def __init__(
        self,
        own: ConnectionPlatformCredentialProvider,
        *,
        account: ClientAccount,
        lenders: PlatformCredentialLenders,
    ) -> None:
        self._own = own
        self._account = account_key(account)
        self._lenders = lenders
        # Which lender issued the last snapshot, so its rejection reaches the
        # channel that can refresh that token.
        self._issued: tuple[str, ConnectionPlatformCredentialProvider] | None = None
        # The ID-token user this session runs as, once known.
        self._user: str | None = None

    def availability(self) -> LenderAvailability:
        return self._lenders.availability(self._account, self._user)

    def _candidates(self) -> list[ConnectionPlatformCredentialProvider]:
        if self._lenders.is_live(self._own):
            return [self._own]
        return self._lenders.lenders_for(self._account, self._user)

    async def resolve(
        self, target: PlatformCredentialTarget
    ) -> PlatformCredentialResult:
        for lender in self._candidates():
            result = self._checked(lender, await lender.resolve(target))
            if result is not None:
                return result
        return PlatformCredentialRequired(reason="transient")

    async def reject(
        self,
        target: PlatformCredentialTarget,
        *,
        observed: PlatformCredentialIdentity,
        reason: Literal["http_unauthorized"],
    ) -> PlatformCredentialResult:
        candidates = self._candidates()
        if self._issued is not None and self._issued[0] == observed.revision:
            issuer = self._issued[1]
            if issuer in candidates:
                candidates = [issuer, *(c for c in candidates if c is not issuer)]
        for lender in candidates:
            replaced = await lender.reject(target, observed=observed, reason=reason)
            result = self._checked(lender, replaced)
            if result is not None:
                return result
        return PlatformCredentialRequired(reason="transient")

    def _checked(
        self,
        lender: ConnectionPlatformCredentialProvider,
        result: PlatformCredentialResult,
    ) -> PlatformCredentialResult | None:
        """``result`` when this session may use it; ``None`` to try the next
        lender, after refusing this one.
        """
        if not isinstance(result, PlatformCredentialSnapshot):
            return result
        owner = lender.owner_of(result.revision)
        if lender is not self._own and result.kind == "oidc_id_token":
            tenant_matches = (
                owner is not None
                and owner.organization_id == normalized_uuid(self._account[0])
            )
            user_matches = owner is not None and self._user in {None, owner.user}
            if not (tenant_matches and user_matches):
                logger.warning(
                    "A borrowed Desktop sign-in does not match the account its "
                    "channel reported (tenant matches: %s, user matches: %s); "
                    "not using it",
                    tenant_matches,
                    user_matches,
                )
                self._lenders.refuse(
                    lender, user=None if not tenant_matches else self._user
                )
                return None
        if owner is not None and self._user is None:
            self._user = owner.user
        self._issued = (result.revision, lender)
        return result

    def has_credential(self) -> bool:
        return True

    def cached_principal(self) -> str | None:
        candidates = self._candidates()
        return candidates[0].cached_principal() if candidates else None


__all__ = [
    "AccountPlatformCredentialProvider",
    "LenderAvailability",
    "PlatformCredentialLenders",
    "account_key",
]
