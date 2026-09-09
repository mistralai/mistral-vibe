from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module, util
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from vibe.core.experiments.models import EvalResponse

_HARNESS_DISTRIBUTION_MODULE = "mistralai_vibe_local_harness"
_VIBE_HARNESS_MODULE = "mistralai_vibe_local_harness.vibe"

_ROLLOUT_EXPERIMENT_KEY = "vibe_cli_unified_harness_rollout"


class ExperimentalHarnessUnavailableError(RuntimeError):
    pass


def experimental_harness_available() -> bool:
    try:
        return util.find_spec(_HARNESS_DISTRIBUTION_MODULE) is not None
    except ModuleNotFoundError:
        return False


def add_experimental_harness_argument(
    parser: argparse.ArgumentParser,
    *,
    group: argparse._MutuallyExclusiveGroup | None = None,
) -> None:
    target: argparse.ArgumentParser | argparse._MutuallyExclusiveGroup = group or parser
    target.add_argument(
        "--experimental-harness",
        action="store_true",
        default=False,
        help=(
            "Use the experimental Unified Harness backend. Requires an "
            "internal Unified Harness installation."
            if experimental_harness_available()
            else argparse.SUPPRESS
        ),
    )


def add_smart_approve_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--smart-approve",
        action="store_true",
        default=False,
        help=(
            "Classify each tool call and auto-run the safe ones, prompting only for "
            "risky ones. Requires --experimental-harness."
            if experimental_harness_available()
            else argparse.SUPPRESS
        ),
    )


HarnessSelectionSource = Literal["flag", "rollout", "default", "flag-legacy"]


@dataclass(frozen=True)
class HarnessSelection:
    """The resolved harness decision, combining CLI flags, the GrowthBook
    rollout cache, and native-module availability.
    """

    use_unified: bool
    source: HarnessSelectionSource


def _rollout_variant_from_cache(cached_eval: object | None) -> str | None:
    """Extract the rollout variant from a cached EvalResponse, or ``None``.

    A pure data lookup — no HTTP client, no ExperimentManager. The cache is
    surface-agnostic (keyed by hashed API key), so this works regardless of
    which surface the previous session ran on.
    """
    if cached_eval is None:
        return None
    features = getattr(cached_eval, "features", None)
    if not isinstance(features, dict):
        return None
    feature = features.get(_ROLLOUT_EXPERIMENT_KEY)
    if feature is None:
        return None
    value = feature.resolved_value()
    if isinstance(value, str):
        return value
    return None


def resolve_harness_selection(
    *,
    experimental_harness: bool,
    legacy_harness: bool,
    cached_eval: EvalResponse | None = None,
) -> HarnessSelection:
    """Resolve which harness to use, combining CLI flags, the GrowthBook
    rollout cache, and native-module availability.

    Precedence (highest wins):
      1. ``--legacy-harness``  -> legacy (escape hatch)
      2. ``--experimental-harness`` -> unified (if available; else fallback)
      3. GrowthBook rollout cache -> unified (if available; else legacy)
      4. Default -> legacy
    """
    if legacy_harness:
        return HarnessSelection(use_unified=False, source="flag-legacy")

    if experimental_harness:
        return HarnessSelection(use_unified=True, source="flag")

    variant = _rollout_variant_from_cache(cached_eval)
    if variant == "unified" and experimental_harness_available():
        return HarnessSelection(use_unified=True, source="rollout")

    return HarnessSelection(use_unified=False, source="default")


def create_experimental_harness_host() -> object:
    try:
        module = import_module(_VIBE_HARNESS_MODULE)
        factory = cast(Callable[[], object], module.create_harness_host)
    except (AttributeError, ModuleNotFoundError) as exc:
        raise ExperimentalHarnessUnavailableError(
            "The Unified Harness backend is not available"
        ) from exc
    # Smart approve needs no Host-global registration: the Runtime tool gate runs
    # the classifier whenever a tool's mode is "classify", which the session's
    # adapter config carries (and can flip live when the mode changes).
    return factory()
