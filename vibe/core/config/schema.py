from __future__ import annotations

from dataclasses import dataclass, field

from pydantic.fields import FieldInfo

from vibe.core.utils.merge import MergeStrategy


class DuplicateMergeMetadataError(TypeError):
    """Raised when a field declares more than one MergeFieldMetadata marker."""


@dataclass(frozen=True)
class MergeFieldMetadata:
    """Base Pydantic Annotated marker that declares how a config field merges across layers.

    Usage::

        active_model: Annotated[str, WithReplaceMerge()] = "devstral-2"
        models: Annotated[list[M], WithUnionMerge(merge_key="alias")]

    Use the concrete subclasses (``WithReplaceMerge``, ``WithConcatMerge``, etc.)
    rather than instantiating this base class directly.
    """

    merge_strategy: MergeStrategy
    merge_key: str | None = None

    @classmethod
    def from_field(cls, field_info: FieldInfo) -> MergeFieldMetadata | None:
        """Extract MergeFieldMetadata from a FieldInfo's metadata, if present.

        Raises DuplicateMergeMetadataError if more than one marker is found.
        """
        result: MergeFieldMetadata | None = None
        for item in field_info.metadata:
            if isinstance(item, cls):
                if result is not None:
                    raise DuplicateMergeMetadataError(
                        f"Field has multiple MergeFieldMetadata markers: "
                        f"{result} and {item}"
                    )
                result = item
        return result


@dataclass(frozen=True)
class WithReplaceMerge(MergeFieldMetadata):
    """Higher layer wins outright."""

    merge_strategy: MergeStrategy = field(default=MergeStrategy.REPLACE, init=False)


@dataclass(frozen=True)
class WithConcatMerge(MergeFieldMetadata):
    """Lists appended in layer order."""

    merge_strategy: MergeStrategy = field(default=MergeStrategy.CONCAT, init=False)


@dataclass(frozen=True)
class WithUnionMerge(MergeFieldMetadata):
    """Lists merged by key, higher layer wins per-key."""

    merge_strategy: MergeStrategy = field(default=MergeStrategy.UNION, init=False)
    merge_key: str = ""

    def __post_init__(self) -> None:
        if not self.merge_key:
            raise TypeError("WithUnionMerge requires merge_key")


@dataclass(frozen=True)
class WithShallowMerge(MergeFieldMetadata):
    """Dicts shallow-merged, absent keys preserved."""

    merge_strategy: MergeStrategy = field(default=MergeStrategy.MERGE, init=False)


@dataclass(frozen=True)
class WithConflictMerge(MergeFieldMetadata):
    """Raises error if more than one layer provides a value."""

    merge_strategy: MergeStrategy = field(default=MergeStrategy.CONFLICT, init=False)
