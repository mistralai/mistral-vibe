from __future__ import annotations

import pytest
import yaml

from vibe.core.skills.parser import (
    SkillParseError,
    parse_openai_skill_metadata,
    parse_skill_markdown,
)


class TestParseSkillMarkdown:
    def test_parses_valid_frontmatter(self) -> None:
        content = """---
name: test-skill
description: A test skill
---

## Body content here
"""
        frontmatter, body = parse_skill_markdown(content)

        assert frontmatter["name"] == "test-skill"
        assert frontmatter["description"] == "A test skill"
        assert "## Body content here" in body

    def test_parses_frontmatter_with_all_fields(self) -> None:
        content = """---
name: full-skill
description: A skill with all fields
license: MIT
compatibility: Requires git
metadata:
  author: Test Author
  version: "1.0"
allowed-tools: bash read
---

Instructions here.
"""
        frontmatter, body = parse_skill_markdown(content)

        assert frontmatter["name"] == "full-skill"
        assert frontmatter["description"] == "A skill with all fields"
        assert frontmatter["license"] == "MIT"
        assert frontmatter["compatibility"] == "Requires git"
        assert frontmatter["metadata"]["author"] == "Test Author"
        assert frontmatter["metadata"]["version"] == "1.0"
        assert frontmatter["allowed-tools"] == "bash read"
        assert "Instructions here." in body

    def test_raises_error_for_missing_frontmatter(self) -> None:
        content = "Just markdown content without frontmatter"

        with pytest.raises(SkillParseError) as exc_info:
            parse_skill_markdown(content)

        assert "Missing or invalid YAML frontmatter" in str(exc_info.value)

    def test_raises_error_for_unclosed_frontmatter(self) -> None:
        content = """---
name: incomplete
description: Missing closing delimiter
"""

        with pytest.raises(SkillParseError) as exc_info:
            parse_skill_markdown(content)

        assert "Missing or invalid YAML frontmatter" in str(exc_info.value)

    def test_raises_error_for_invalid_yaml(self) -> None:
        content = """---
name: [invalid yaml
description: broken
---

Body here.
"""

        with pytest.raises(SkillParseError) as exc_info:
            parse_skill_markdown(content)

        assert "Invalid YAML frontmatter" in str(exc_info.value)

    def test_accepts_unquoted_colon_in_value(self) -> None:
        content = """---
name: load-test-definitions
description: Load this skill: - when the user wants to read test definitions
---
"""
        frontmatter, _ = parse_skill_markdown(content)

        assert frontmatter["name"] == "load-test-definitions"
        assert (
            frontmatter["description"]
            == "Load this skill: - when the user wants to read test definitions"
        )

    @pytest.mark.parametrize(
        ("description_yaml", "expected"),
        [
            ("description: Don't use: for this # note\n", "Don't use: for this"),
            ("description: Use this skill when:\n", "Use this skill when:"),
            (
                "description: Use when: X\n  and more text\n",
                "Use when: X and more text",
            ),
            ("description: [WIP] Use when: X\n", "[WIP] Use when: X"),
            ("name: a: b\ndescription: Use when: X\n", "Use when: X"),
            (
                "description: Use when: X\n  and more\nname: a: b\n",
                "Use when: X and more",
            ),
        ],
    )
    def test_repairs_colon_values(self, description_yaml: str, expected: str) -> None:
        frontmatter, _ = parse_skill_markdown(f"---\n{description_yaml}---\n")

        assert frontmatter["description"] == expected

    def test_repairs_colon_value_of_nested_key(self) -> None:
        content = """---
metadata:
  short-description: Use when: X
  author: me
---
"""
        frontmatter, _ = parse_skill_markdown(content)

        assert frontmatter["metadata"] == {
            "short-description": "Use when: X",
            "author": "me",
        }

    @pytest.mark.parametrize(
        "other_field",
        [
            "notes: |\n  Step: one\n  Step: two\n",
            "examples:\n  - |\n    run: a: b\n",
            "steps:\n  - >-\n    step: do: this\n",
            "notes: !!str |\n  k: v: w\n",
            "notes: &a |\n  k: v: w\n",
            "notes:\n  |\n  k: v: w\n",
            'notes: "first line\n  second: a: b\n  end"\n',
            "notes: 'first line\n  second: a: b'\n",
            "meta: {a: 1,\n  b: 2}\n",
            "items:\n  - a: Use\n    b: y\n",
            'notes: "first\nsecond: a: b\nend"\n',
            'meta: {a: "x: y",\nb: c}\n',
            "notes:\n  # c\n  |\n  k: v: w\n",
        ],
    )
    @pytest.mark.parametrize("description_first", [True, False])
    def test_repair_leaves_other_valid_fields_untouched(
        self, other_field: str, description_first: bool
    ) -> None:
        def frontmatter_with(description: str) -> str:
            if description_first:
                return f"{description}\n{other_field}"
            return f"{other_field}{description}\n"

        frontmatter, _ = parse_skill_markdown(
            f"---\n{frontmatter_with('description: Use when: X')}---\n"
        )

        assert frontmatter == yaml.safe_load(
            frontmatter_with("description: 'Use when: X'")
        )

    @pytest.mark.parametrize(
        "frontmatter_yaml",
        [
            "description: &d Use when: X\n",
            "description: Use this skill\n  when: X happens\n",
            "description: Does stuff\n  allowed-tools: Read, Grep\n",
            "description: Use when: X\n  allowed-tools: Read\n",
            "description: Use when: X\n  steps:\n    - a\n",
            "description: Use when: X # note\n  more text\n",
            "meta: {\n  b: z,\n  a: x: y,\n}\n",
            "tags: [\n  a,\n  b: c: d,\n]\n",
        ],
    )
    def test_does_not_repair_ambiguous_values(self, frontmatter_yaml: str) -> None:
        with pytest.raises(SkillParseError) as exc_info:
            parse_skill_markdown(f"---\n{frontmatter_yaml}---\n")

        assert "Invalid YAML frontmatter" in str(exc_info.value)

    def test_reports_original_error_when_repair_does_not_help(self) -> None:
        content = """---
name: [invalid yaml
description: Use when: tests fail
---
"""

        with pytest.raises(SkillParseError) as exc_info:
            parse_skill_markdown(content)

        assert "Invalid YAML frontmatter" in str(exc_info.value)
        assert "line 2" in str(exc_info.value)

    def test_raises_error_for_non_dict_frontmatter(self) -> None:
        content = """---
- item1
- item2
---

Body here.
"""

        with pytest.raises(SkillParseError) as exc_info:
            parse_skill_markdown(content)

        assert "must be a mapping" in str(exc_info.value)

    def test_handles_empty_frontmatter(self) -> None:
        content = """---
---

Body content.
"""
        frontmatter, body = parse_skill_markdown(content)

        assert frontmatter == {}
        assert "Body content." in body

    def test_handles_frontmatter_with_no_body(self) -> None:
        content = """---
name: minimal
description: No body
---
"""
        frontmatter, body = parse_skill_markdown(content)

        assert frontmatter["name"] == "minimal"
        assert body.strip() == ""


class TestParseOpenAISkillMetadata:
    def test_accepts_upstream_product_policy(self) -> None:
        metadata = parse_openai_skill_metadata("policy:\n  products:\n    - chatgpt\n")

        assert metadata.policy is not None
        assert metadata.policy.products == ["chatgpt"]
        assert metadata.has_unhandled_fields is True

    def test_rejects_unknown_policy_field(self) -> None:
        with pytest.raises(SkillParseError) as exc_info:
            parse_openai_skill_metadata("policy:\n  allow_implicit_invocaton: false\n")

        assert "allow_implicit_invocaton" in str(exc_info.value)
