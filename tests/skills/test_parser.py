from __future__ import annotations

import pytest

from vibe.core.skills.parser import SkillParseError, parse_frontmatter


class TestParseFrontmatter:
    def test_parses_valid_frontmatter(self) -> None:
        content = """---
name: test-skill
description: A test skill
---

## Body content here
"""
        frontmatter, body = parse_frontmatter(content)

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
allowed-tools: bash read_file
---

Instructions here.
"""
        frontmatter, body = parse_frontmatter(content)

        assert frontmatter["name"] == "full-skill"
        assert frontmatter["description"] == "A skill with all fields"
        assert frontmatter["license"] == "MIT"
        assert frontmatter["compatibility"] == "Requires git"
        assert frontmatter["metadata"]["author"] == "Test Author"
        assert frontmatter["metadata"]["version"] == "1.0"
        assert frontmatter["allowed-tools"] == "bash read_file"
        assert "Instructions here." in body

    def test_raises_error_for_missing_frontmatter(self) -> None:
        content = "Just markdown content without frontmatter"

        with pytest.raises(SkillParseError) as exc_info:
            parse_frontmatter(content)

        assert "Missing or invalid YAML frontmatter" in str(exc_info.value)

    def test_raises_error_for_unclosed_frontmatter(self) -> None:
        content = """---
name: incomplete
description: Missing closing delimiter
"""

        with pytest.raises(SkillParseError) as exc_info:
            parse_frontmatter(content)

        assert "Missing or invalid YAML frontmatter" in str(exc_info.value)

    def test_raises_error_for_invalid_yaml(self) -> None:
        content = """---
name: [invalid yaml
description: broken
---

Body here.
"""

        with pytest.raises(SkillParseError) as exc_info:
            parse_frontmatter(content)

        assert "Invalid YAML frontmatter" in str(exc_info.value)

    def test_raises_error_for_non_dict_frontmatter(self) -> None:
        content = """---
- item1
- item2
---

Body here.
"""

        with pytest.raises(SkillParseError) as exc_info:
            parse_frontmatter(content)

        assert "must be a mapping" in str(exc_info.value)

    def test_handles_empty_frontmatter(self) -> None:
        content = """---
---

Body content.
"""
        frontmatter, body = parse_frontmatter(content)

        assert frontmatter == {}
        assert "Body content." in body

    def test_handles_frontmatter_with_no_body(self) -> None:
        content = """---
name: minimal
description: No body
---
"""
        frontmatter, body = parse_frontmatter(content)

        assert frontmatter["name"] == "minimal"
        assert body.strip() == ""
