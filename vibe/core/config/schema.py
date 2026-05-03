from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from vibe.core.utils.merge import MergeStrategy


class DuplicateMergeMetadataError(TypeError):
    """Raised when a field declares more than one MergeFieldMetadata marker."""


class ConfigDefinitionError(TypeError):
    """Raised when a config schema or fragment is declared with invalid fields."""


class ConfigSchema(BaseModel):
    """Base for composite config schemas composed of fragments and merge-aware fields."""

    @classmethod
    def __pydantic_on_complete__(cls) -> None:
        super().__pydantic_on_complete__()

        if cls.__name__ == "ConfigSchema" and cls.__module__ == __name__:
            return

        for field_name, field_info in cls.model_fields.items():
            has_merge_metadata = MergeFieldMetadata.from_field(field_info) is not None
            is_fragment = isinstance(field_info.annotation, type) and issubclass(
                field_info.annotation, ConfigFragment
            )

            if is_fragment and has_merge_metadata:
                raise ConfigDefinitionError(
                    f"{cls.__name__}.{field_name} is a ConfigFragment field and "
                    "must not declare merge metadata"
                )

            if is_fragment or has_merge_metadata:
                continue

            raise ConfigDefinitionError(
                f"{cls.__name__}.{field_name} must declare merge metadata or use a "
                "ConfigFragment subclass"
            )


class ConfigFragment(BaseModel):
    """Base for domain config groups with merge-aware top-level fields."""

    @classmethod
    def __pydantic_on_complete__(cls) -> None:
        super().__pydantic_on_complete__()

        if cls.__name__ == "ConfigFragment" and cls.__module__ == __name__:
            return

        for field_name, field_info in cls.model_fields.items():
            if MergeFieldMetadata.from_field(field_info) is not None:
                continue

            raise ConfigDefinitionError(
                f"{cls.__name__}.{field_name} must declare merge metadata via "
                "MergeFieldMetadata"
            )


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
