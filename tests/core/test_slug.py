from __future__ import annotations

from vibe.core.slug import _ADJECTIVES, _NOUNS, create_slug


class TestCreateSlug:
    def test_format_is_adj_adj_noun(self) -> None:
        slug = create_slug()
        parts = slug.split("-")
        assert len(parts) == 3

    def test_parts_from_word_pools(self) -> None:
        slug = create_slug()
        adj1, adj2, noun = slug.split("-")
        assert adj1 in _ADJECTIVES
        assert adj2 in _ADJECTIVES
        assert noun in _NOUNS

    def test_adjectives_are_distinct(self) -> None:
        for _ in range(20):
            adj1, adj2, _ = create_slug().split("-")
            assert adj1 != adj2

    def test_randomness_produces_variety(self) -> None:
        slugs = {create_slug() for _ in range(20)}
        assert len(slugs) > 1

    def test_word_pools_non_empty(self) -> None:
        assert len(_ADJECTIVES) > 0
        assert len(_NOUNS) > 0

    def test_word_pools_have_no_duplicates(self) -> None:
        assert len(_ADJECTIVES) == len(set(_ADJECTIVES))
        assert len(_NOUNS) == len(set(_NOUNS))
