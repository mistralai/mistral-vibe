from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict


class RawConfig(BaseModel):
    """Permissive default schema that preserves all fields as extras."""

    model_config = ConfigDict(extra="allow")


class ConfigLayerError(Exception):
    """Base error for all ConfigLayer errors."""

    def __init__(self, layer_name: str, message: str) -> None:
        super().__init__(message)
        self.layer_name = layer_name


class UntrustedLayerError(ConfigLayerError):
    """Raised when attempting to load data from an untrusted layer."""

    def __init__(self, layer_name: str) -> None:
        super().__init__(layer_name, f"Layer '{layer_name}' is not trusted")


class EmptyLayerError(ConfigLayerError):
    """Raised when a trusted layer has no data."""

    def __init__(self, layer_name: str) -> None:
        super().__init__(layer_name, f"Layer '{layer_name}' has no data after load")


class TrustResolutionError(ConfigLayerError):
    """Raised when trust status is not resolvable."""

    def __init__(self, layer_name: str) -> None:
        super().__init__(
            layer_name,
            f"Layer '{layer_name}': _check_trust() must return bool, got None",
        )


class LayerImplementationError(ConfigLayerError):
    """Raised when a subclass-provided method fails."""

    def __init__(self, layer_name: str, method_name: str) -> None:
        super().__init__(layer_name, f"Layer '{layer_name}': {method_name}() failed")
        self.method_name = method_name


@dataclass(frozen=True, slots=True)
class _LayerState[S: BaseModel]:
    is_trusted: bool | None = None
    data: S | None = None


@dataclass(frozen=True, slots=True)
class _GrantTrust:
    pass


@dataclass(frozen=True, slots=True)
class _RevokeTrust:
    pass


@dataclass(frozen=True, slots=True)
class _ResolveTrust:
    pass


@dataclass(frozen=True, slots=True)
class _Load:
    force: bool = False


@dataclass(frozen=True, slots=True)
class _InvalidateCache:
    pass


class ConfigLayer[S: BaseModel](ABC):
    """Each layer represents a named source that produces a sparse
    dictionary of configuration values.
    """

    def __init__(self, *, name: str, output_schema: type[S] = RawConfig) -> None:
        self.name = name
        self.output_schema = output_schema

        self._state: _LayerState[S] = _LayerState()
        self._lock = asyncio.Lock()

    # --- Overridable ---

    async def _check_trust(self) -> bool:
        """Resolve whether this layer should be trusted.

        Override in subclasses to implement custom trust logic.
        E.g. a user-local layer returns ``True``,
        a project layer checks persisted permissions.

        Returns ``False`` by default (untrusted).
        """
        return False

    @abstractmethod
    async def _read_config(self) -> dict[str, Any]:
        """Read and return sparse dict from this layer's backing store.

        Subclasses only need to implement the raw read logic; caching
        is handled by the base.
        """
        ...

    async def _on_trust_changed(self, old: bool | None, new: bool | None) -> None:
        """Called when the trust status changes.

        Override to persist trust status or react to trust transitions.
        Default is a no-op.
        """
        return

    # --- Internal ---

    async def _notify_trust_change(self, old: bool | None, new: bool | None) -> None:
        """Call ``_on_trust_changed`` and wrap any error."""
        if old == new:
            return
        try:
            await self._on_trust_changed(old, new)
        except Exception as e:
            raise LayerImplementationError(self.name, "_on_trust_changed") from e

    async def _resolve_check_trust(self) -> bool:
        """Call ``_check_trust`` and wrap any error."""
        try:
            return await self._check_trust()
        except Exception as e:
            raise LayerImplementationError(self.name, "_check_trust") from e

    async def _dispatch(self, action: Any) -> _LayerState[S]:
        """Serialize all state mutations through a single lock."""
        async with self._lock:
            state = _LayerState(
                is_trusted=self._state.is_trusted, data=self._state.data
            )

            match action:
                case _GrantTrust():
                    new_state = await self._handle_grant_trust(state)
                case _RevokeTrust():
                    new_state = await self._handle_revoke_trust(state)
                case _ResolveTrust():
                    new_state = await self._handle_resolve_trust(state)
                case _Load(force=force):
                    new_state = await self._handle_load(state, force)
                case _InvalidateCache():
                    new_state = await self._handle_invalidate_cache(state)
                case _:
                    raise NotImplementedError(f"Unknown action: {action!r}")

            self._state = new_state

            return new_state

    async def _handle_grant_trust(self, state: _LayerState[S]) -> _LayerState[S]:
        if state.is_trusted is True:
            return state

        await self._notify_trust_change(state.is_trusted, True)

        return _LayerState(is_trusted=True, data=state.data)

    async def _handle_revoke_trust(self, state: _LayerState[S]) -> _LayerState[S]:
        if state.is_trusted is False:
            return state

        await self._notify_trust_change(state.is_trusted, False)

        return _LayerState(is_trusted=False, data=None)

    async def _handle_resolve_trust(self, state: _LayerState[S]) -> _LayerState[S]:
        is_trusted = await self._resolve_check_trust()

        await self._notify_trust_change(state.is_trusted, is_trusted)

        return _LayerState(
            is_trusted=is_trusted, data=state.data if is_trusted else None
        )

    async def _handle_load(self, state: _LayerState[S], force: bool) -> _LayerState[S]:
        if state.is_trusted is not None:
            is_trusted = state.is_trusted
        else:
            is_trusted = await self._resolve_check_trust()

        await self._notify_trust_change(state.is_trusted, is_trusted)

        if not is_trusted:
            return _LayerState(is_trusted=is_trusted, data=None)

        next_state = _LayerState(is_trusted=is_trusted, data=state.data)

        if next_state.data is None or force:
            try:
                raw = await self._read_config()
            except Exception as e:
                raise LayerImplementationError(self.name, "_read_config") from e
            next_state = _LayerState(
                is_trusted=next_state.is_trusted, data=self.validate_output(raw)
            )

        return next_state

    async def _handle_invalidate_cache(self, state: _LayerState[S]) -> _LayerState[S]:
        return _LayerState(is_trusted=state.is_trusted, data=None)

    # --- Public ---

    @property
    def is_trusted(self) -> bool | None:
        """Current trust status. ``None`` if unresolved."""
        return self._state.is_trusted

    async def resolve_trust(self) -> bool:
        """Resolve and cache trust status."""
        state = await self._dispatch(_ResolveTrust())

        if state.is_trusted is None:
            raise TrustResolutionError(self.name)

        return state.is_trusted

    async def grant_trust(self) -> None:
        """Explicitly mark this layer as trusted."""
        await self._dispatch(_GrantTrust())

    async def revoke_trust(self) -> None:
        """Explicitly mark this layer as untrusted and clear cached data."""
        await self._dispatch(_RevokeTrust())

    async def invalidate_cache(self) -> None:
        """Clear the in-memory cache so the next ``load()`` re-reads."""
        await self._dispatch(_InvalidateCache())

    async def load(self, *, force: bool = False) -> S:
        """Load data from this layer, enforcing trust and caching the result.

        Use ``force=True`` to bypass caching.
        """
        state = await self._dispatch(_Load(force=force))

        if state.is_trusted is None:
            raise TrustResolutionError(self.name)

        if not state.is_trusted:
            raise UntrustedLayerError(self.name)

        if state.data is None:
            raise EmptyLayerError(self.name)

        return state.data.model_copy(deep=True)

    async def get_fingerprint(self) -> str:
        """Return opaque token representing current backing store state."""
        raise NotImplementedError

    async def apply(self, patch: Any, *, on_conflict: str = "cancel") -> None:
        """Persist a patch to this layer's backing store."""
        raise NotImplementedError

    def validate_output(self, data: dict[str, Any]) -> S:
        """Validate *data* against ``output_schema``."""
        return self.output_schema.model_validate(data)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r})"
