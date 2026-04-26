from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from vibe.core.utils.merge import MergeConflictError, MergeKeyError, MergeStrategy


class TestMergeStrategyEnum:
    def test_values_are_lowercase_strings(self) -> None:
        assert MergeStrategy.REPLACE == "replace"
        assert MergeStrategy.CONCAT == "concat"
        assert MergeStrategy.UNION == "union"
        assert MergeStrategy.MERGE == "merge"
        assert MergeStrategy.CONFLICT == "conflict"

    def test_all_strategies_exist(self) -> None:
        assert len(MergeStrategy) == 5

    def test_unknown_strategy_raises_not_implemented(self) -> None:
        fake = MagicMock(spec=MergeStrategy)
        with pytest.raises(NotImplementedError, match="not implemented"):
            MergeStrategy.apply(fake, "a", "b")


class TestReplace:
    def test_override_wins(self) -> None:
        assert MergeStrategy.REPLACE.apply(1, 2) == 2

    def test_base_returned_when_override_none(self) -> None:
        assert MergeStrategy.REPLACE.apply(1, None) == 1

    def test_both_none_returns_none(self) -> None:
        assert MergeStrategy.REPLACE.apply(None, None) is None

    def test_override_none_preserves_base_type(self) -> None:
        assert MergeStrategy.REPLACE.apply([1, 2], None) == [1, 2]
        assert MergeStrategy.REPLACE.apply({"a": 1}, None) == {"a": 1}

    def test_override_can_be_falsy(self) -> None:
        assert MergeStrategy.REPLACE.apply(1, 0) == 0
        assert MergeStrategy.REPLACE.apply(1, "") == ""
        assert MergeStrategy.REPLACE.apply(1, False) is False


class TestConcat:
    def test_lists_concatenated(self) -> None:
        assert MergeStrategy.CONCAT.apply([1, 2], [3, 4]) == [1, 2, 3, 4]

    def test_base_none_returns_override(self) -> None:
        assert MergeStrategy.CONCAT.apply(None, [1]) == [1]

    def test_override_none_returns_base(self) -> None:
        assert MergeStrategy.CONCAT.apply([1], None) == [1]

    def test_both_none_returns_none(self) -> None:
        assert MergeStrategy.CONCAT.apply(None, None) is None

    def test_empty_lists(self) -> None:
        assert MergeStrategy.CONCAT.apply([], []) == []

    def test_raises_type_error_for_non_list(self) -> None:
        with pytest.raises(TypeError, match="CONCAT requires list operands"):
            MergeStrategy.CONCAT.apply("a", [1])
        with pytest.raises(TypeError, match="CONCAT requires list operands"):
            MergeStrategy.CONCAT.apply([1], "b")


class TestUnion:
    def test_merge_by_key_override_wins(self) -> None:
        base = [{"name": "a", "v": 1}, {"name": "b", "v": 2}]
        override = [{"name": "b", "v": 99}]
        result = MergeStrategy.UNION.apply(base, override, key_fn=lambda x: x["name"])
        assert result == [{"name": "a", "v": 1}, {"name": "b", "v": 99}]

    def test_preserves_order_base_first(self) -> None:
        base = [{"name": "a"}, {"name": "b"}]
        override = [{"name": "c"}]
        result = MergeStrategy.UNION.apply(base, override, key_fn=lambda x: x["name"])
        assert [x["name"] for x in result] == ["a", "b", "c"]

    def test_new_keys_from_override_appended(self) -> None:
        base = [{"name": "a"}]
        override = [{"name": "b"}, {"name": "c"}]
        result = MergeStrategy.UNION.apply(base, override, key_fn=lambda x: x["name"])
        assert len(result) == 3

    def test_base_none_returns_override(self) -> None:
        assert MergeStrategy.UNION.apply(None, [1], key_fn=str) == [1]

    def test_override_none_returns_base(self) -> None:
        assert MergeStrategy.UNION.apply([1], None, key_fn=str) == [1]

    def test_both_none_returns_none(self) -> None:
        assert MergeStrategy.UNION.apply(None, None) is None

    def test_raises_without_key_fn(self) -> None:
        with pytest.raises(ValueError, match="UNION strategy requires key_fn"):
            MergeStrategy.UNION.apply([1], [2])

    def test_raises_type_error_for_non_list(self) -> None:
        with pytest.raises(TypeError, match="UNION requires list operands"):
            MergeStrategy.UNION.apply("a", [1], key_fn=str)

    def test_raises_merge_key_error_for_missing_key(self) -> None:
        base = [{"name": "a", "v": 1}]
        override = [{"v": 2}]  # missing "name"
        with pytest.raises(MergeKeyError) as exc_info:
            MergeStrategy.UNION.apply(base, override, key_fn=lambda x: x["name"])
        assert exc_info.value.key == "name"


class TestMerge:
    def test_dicts_merged_one_level(self) -> None:
        result = MergeStrategy.MERGE.apply({"a": 1, "b": 2}, {"b": 3, "c": 4})
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_dicts_not_recursed(self) -> None:
        base = {"a": {"x": 1, "y": 2}}
        override = {"a": {"y": 3, "z": 4}}
        result = MergeStrategy.MERGE.apply(base, override)
        assert result == {"a": {"y": 3, "z": 4}}

    def test_base_none_returns_override(self) -> None:
        assert MergeStrategy.MERGE.apply(None, {"a": 1}) == {"a": 1}

    def test_override_none_returns_base(self) -> None:
        assert MergeStrategy.MERGE.apply({"a": 1}, None) == {"a": 1}

    def test_both_none_returns_none(self) -> None:
        assert MergeStrategy.MERGE.apply(None, None) is None

    def test_raises_type_error_for_non_dict(self) -> None:
        with pytest.raises(TypeError, match="MERGE requires dict operands"):
            MergeStrategy.MERGE.apply([1], {"a": 1})

    def test_does_not_mutate_inputs(self) -> None:
        base = {"a": 1}
        override = {"b": 2}
        MergeStrategy.MERGE.apply(base, override)
        assert base == {"a": 1}
        assert override == {"b": 2}


class TestConflict:
    def test_raises_when_both_provided(self) -> None:
        with pytest.raises(MergeConflictError):
            MergeStrategy.CONFLICT.apply(1, 2)

    def test_base_only_returns_base(self) -> None:
        assert MergeStrategy.CONFLICT.apply(1, None) == 1

    def test_override_only_returns_override(self) -> None:
        assert MergeStrategy.CONFLICT.apply(None, 2) == 2

    def test_both_none_returns_none(self) -> None:
        assert MergeStrategy.CONFLICT.apply(None, None) is None


class TestMergeConflictError:
    def test_default_message(self) -> None:
        err = MergeConflictError()
        assert str(err) == "Merge conflict"
        assert err.field_name == ""

    def test_message_with_field_name(self) -> None:
        err = MergeConflictError("active_model")
        assert str(err) == "Merge conflict on field 'active_model'"
        assert err.field_name == "active_model"


class TestMergeKeyError:
    def test_message_includes_key_and_item(self) -> None:
        item = {"v": 2}
        err = MergeKeyError("name", item)
        assert "name" in str(err)
        assert str(item) in str(err)
        assert err.key == "name"
        assert err.item == item

    def test_is_key_error(self) -> None:
        assert issubclass(MergeKeyError, KeyError)
