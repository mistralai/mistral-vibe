from __future__ import annotations

from dataclasses import dataclass, field
from typing import Set, List, Dict, Optional, Any, Tuple
import os
from pathlib import Path
import re
from collections import defaultdict

from .tags import TagExtractor, Tag, ExtractionError, ExtractionResult
from .graph import build_graph, rank_files, distribute_rank
from .rendering import render_repo_map


# ============================================================================
# Centralized Configuration for Ranking Weights
# ============================================================================
@dataclass(frozen=True)
class RepoMapConfig:
    """Centralized configuration for RepoMap ranking weights and thresholds.

    All magic numbers are consolidated here for easy tuning and A/B testing.
    """

    # Path-based multipliers
    path_boost_multiplier: float = 2.0
    path_test_multiplier: float = 0.4
    path_framework_multiplier: float = 0.5

    # Personalization boosts
    chat_file_boost: float = 10.0
    mentioned_file_boost: float = 5.0
    def_token_match_boost: float = 20.0
    def_prefix_match_boost: float = 15.0
    path_mention_match_boost: float = 10.0
    ref_token_match_boost: float = 8.0
    ref_name_match_boost: float = 25.0

    # Graph edge weights
    mention_match_weight: float = 10.0
    structural_boost_weight: float = 10.0
    private_symbol_penalty: float = 0.1
    common_symbol_threshold: int = 5  # Files defining same symbol triggers damping
    short_ident_penalty: float = 0.1
    chat_file_edge_weight: float = 50.0
    self_edge_weight: float = 100.0

    # IDF-based damping parameters
    use_idf_damping: bool = True
    idf_base: float = 1.5  # logarithm base for IDF calculation


# Default config instance
_DEFAULT_CONFIG = RepoMapConfig()


# Path-based scoring adjustments
_PATH_BOOST_SEGMENTS = {"entrypoint"}
_PATH_TEST_SEGMENTS = {"tests", "__tests__", "spec", "specs"}
_FRAMEWORK_PATH_SEGMENTS = {"framework", "frameworks", "vendor", "third_party", "third-party"}
_PATH_BOOST_MULTIPLIER = _DEFAULT_CONFIG.path_boost_multiplier
_PATH_TEST_MULTIPLIER = _DEFAULT_CONFIG.path_test_multiplier
_PATH_FRAMEWORK_MULTIPLIER = _DEFAULT_CONFIG.path_framework_multiplier

# Known technical phrase synonyms for query expansion
# Maps lowercase phrase -> normalized identifier
_PHRASE_SYNONYMS: dict[str, str] = {
    "user authentication": "user_auth",
    "access token": "access_token",
    "api key": "api_key",
    "data model": "data_model",
    "database connection": "db_connection",
    "file upload": "file_upload",
    "error handler": "error_handler",
    "event loop": "event_loop",
    "memory cache": "cache",
    "rate limit": "rate_limit",
    "web socket": "websocket",
    "http request": "http_request",
    "http response": "http_response",
}

_STOPWORDS = {
    "a",
    "about",
    "after",
    "all",
    "also",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "but",
    "by",
    "can",
    "could",
    "define",
    "defines",
    "do",
    "does",
    "done",
    "each",
    "files",
    "find",
    "for",
    "from",
    "get",
    "had",
    "has",
    "have",
    "here",
    "how",
    "if",
    "implemented",
    "in",
    "into",
    "is",
    "it",
    "its",
    "just",
    "like",
    "make",
    "many",
    "more",
    "most",
    "my",
    "need",
    "new",
    "not",
    "now",
    "of",
    "on",
    "one",
    "only",
    "or",
    "other",
    "our",
    "out",
    "over",
    "repo",
    "see",
    "should",
    "show",
    "so",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "to",
    "up",
    "use",
    "used",
    "using",
    "very",
    "want",
    "was",
    "way",
    "we",
    "well",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "will",
    "with",
    "would",
    "work",
    "you",
    "your",
}


def _extract_ident_tokens(ident: str) -> set[str]:
    """Extract tokens from an identifier, supporting camelCase, snake_case, and kebab-case.

    Args:
        ident: The identifier string to tokenize.

    Returns:
        Set of lowercase tokens extracted from the identifier.
    """
    # Normalize: replace non-alphanumeric except underscores and hyphens with space
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", " ", ident)
    # Split on underscores, hyphens, and whitespace (supports snake_case and kebab-case)
    parts = re.split(r"[_\-\s]+", normalized)
    tokens: set[str] = set()
    for part in parts:
        if not part:
            continue
        # Split camelCase: "myComponent" -> ["my", "Component"]
        camel_parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", part)
        if camel_parts:
            tokens.update(camel_parts)
        else:
            tokens.add(part)
    lowered = {t.lower() for t in tokens if t}
    if any(token.startswith("auth") for token in lowered):
        lowered.add("auth")
    return lowered


def extract_mentions_from_text(text: str) -> set[str]:
    """Extract meaningful identifiers from query text.

    Filters stopwords and only creates phrases for code-like patterns
    (CamelCase, snake_case, or known technical phrases).
    """
    words = re.findall(r"[a-zA-Z0-9_]+", text)
    # Filter stopwords and short words
    filtered = [
        word for word in words
        if len(word) > 2 and word.lower() not in _STOPWORDS
    ]

    mentions: set[str] = set(filtered)

    # Only add phrases that look like code identifiers or known technical terms
    for index in range(len(filtered) - 1):
        word1, word2 = filtered[index], filtered[index + 1]
        phrase = f"{word1} {word2}"
        normalized = phrase.lower()
        # Add phrase if it's a known synonym phrase
        if normalized in _PHRASE_SYNONYMS:
            mentions.add(phrase)
        # Or if both words seem technical (CamelCase, or longer than 4 chars)
        elif _is_technical_word(word1) and _is_technical_word(word2):
            mentions.add(phrase)

    return mentions


def _is_technical_word(word: str) -> bool:
    """Check if a word looks like a technical/code identifier."""
    # CamelCase pattern
    if re.match(r"^[A-Z][a-z]+[A-Z]", word):
        return True
    # Contains underscore (snake_case fragment)
    if "_" in word:
        return True
    # Longer words are more likely technical
    if len(word) >= 6:
        return True
    return False


def _path_contains_segment(path: Path, segments: set[str]) -> bool:
    lowered_parts = [part.lower() for part in path.parts]
    return any(segment in part for segment in segments for part in lowered_parts)


def _is_test_path(path: Path) -> bool:
    lowered_parts = {part.lower() for part in path.parts}
    if lowered_parts.intersection(_PATH_TEST_SEGMENTS):
        return True
    stem = path.stem.lower()
    return stem.startswith("test_") or stem.endswith("_test") or stem.endswith("_spec")


def _path_multiplier(path: Path) -> float:
    multiplier = 1.0
    if _is_test_path(path):
        multiplier *= _PATH_TEST_MULTIPLIER
    if _path_contains_segment(path, _FRAMEWORK_PATH_SEGMENTS):
        multiplier *= _PATH_FRAMEWORK_MULTIPLIER
    if _path_contains_segment(path, _PATH_BOOST_SEGMENTS):
        multiplier *= _PATH_BOOST_MULTIPLIER
    return multiplier


def _path_priority_adjustment(path: Path) -> int:
    score = 0
    if _is_test_path(path):
        score -= 2
    if _path_contains_segment(path, _FRAMEWORK_PATH_SEGMENTS):
        score -= 1
    if _path_contains_segment(path, _PATH_BOOST_SEGMENTS):
        score += 2
    return score


def _is_camelcase_or_snake(ident: str) -> bool:
    """Check if identifier looks like code (CamelCase or snake_case)."""
    has_camel = bool(re.search(r"[a-z][A-Z]", ident))
    has_snake = "_" in ident and any(c.isalpha() for c in ident)
    return has_camel or has_snake


def _normalize_mentions(mentions: set[str]) -> set[str]:
    """Normalize mentions to tokens for matching.

    This function extracts identifiers from mentions and normalizes them
    for exact matching. It does NOT expand to synonyms to avoid semantic drift.
    """
    normalized: set[str] = set()
    for mention in mentions:
        if len(mention) <= 2:
            continue
        # Preserve CamelCase and snake_case identifiers as-is (lowercase)
        # These are likely exact code references
        if _is_camelcase_or_snake(mention):
            normalized.add(mention.lower())
        tokens = _extract_ident_tokens(mention)
        for token in tokens:
            if len(token) <= 2 or token in _STOPWORDS:
                continue
            normalized.add(token)
    return normalized


def _build_mention_index(mentions: set[str]) -> tuple[set[str], dict[str, set[str]]]:
    """Build an inverted index for fast mention matching.

    Returns:
        Tuple of (mention_set, prefix_index) where prefix_index maps
        4-char prefixes to the full mentions that have that prefix.
    """
    prefix_index: dict[str, set[str]] = defaultdict(set)
    for mention in mentions:
        if len(mention) >= 4:
            prefix_index[mention[:4].lower()].add(mention.lower())
    return {m.lower() for m in mentions}, prefix_index


def _tokens_match_with_index(
    tokens: set[str],
    mention_set: set[str],
    prefix_index: dict[str, set[str]],
) -> bool:
    """Check if any token matches a mention using the inverted index."""
    for token in tokens:
        token_lower = token.lower()
        # Exact match (O(1) lookup)
        if token_lower in mention_set:
            return True
        # Prefix match using index (O(1) lookup per token)
        if len(token_lower) >= 4:
            prefix = token_lower[:4]
            if prefix in prefix_index:
                # Found candidates with same prefix
                return True
    return False


def _priority_score_indexed(
    fname: str,
    ident: str,
    mention_set: set[str],
    prefix_index: dict[str, set[str]],
    tags: set[Tag],
    ref_tokens: set[str],
    chat_file_dirs: set[str] | None = None,
) -> int:
    """Compute priority score using indexed mention lookup.

    Args:
        fname: File path being scored
        ident: Identifier name being scored
        mention_set: Set of mentioned identifiers (lowercased)
        prefix_index: Prefix index for fast lookup
        tags: Tags associated with this definition
        ref_tokens: Reference tokens in this file
        chat_file_dirs: Set of directory prefixes from chat files (for context weighting)
    """
    score = 0
    ident_tokens = _extract_ident_tokens(ident)
    tag_tokens: set[str] = set()
    for tag in tags:
        tag_tokens.update(_extract_ident_tokens(tag.name))
    tokens = ident_tokens.union(tag_tokens).union(ref_tokens)
    tokens_lower = {t.lower() for t in tokens}

    # Direct exact match on identifier (case insensitive) - highest priority
    ident_lower = ident.lower()
    if ident_lower in mention_set:
        score += 5
    # NOTE: Removed substring matching (e.g., "auth" matching "authenticate")
    # This was too aggressive and caused semantic drift

    # Token-based matching - require exact token match only
    # (removed prefix matching which was too loose)
    if tokens_lower.intersection(mention_set):
        score += 3

    # Path matching - only if exact directory/file name component matches
    fname_parts = set(Path(fname).parts)
    fname_parts_lower = {p.lower() for p in fname_parts}
    if fname_parts_lower.intersection(mention_set):
        score += 2

    # Directory-based context weighting: boost files near chat files, penalize distant ones
    if chat_file_dirs:
        fname_dir = str(Path(fname).parent)
        # Check if file is in same directory tree as any chat file
        in_context = any(
            fname_dir.startswith(chat_dir) or chat_dir.startswith(fname_dir)
            for chat_dir in chat_file_dirs
        )
        if in_context:
            score += 1
        else:
            # Penalize files in unrelated directories
            score -= 1

    score += _path_priority_adjustment(Path(fname))
    return score


def _prioritize_ranked_defs(
    ranked_defs: list[tuple[tuple[str, str], float]],
    definitions: dict[tuple[str, str], set[Tag]],
    mentions: set[str],
    ref_tokens_by_file: dict[str, set[str]],
    ref_names_by_file: dict[str, set[str]] | None = None,
    chat_file_dirs: set[str] | None = None,
) -> list[tuple[tuple[str, str], float]]:
    """Prioritize definitions that match mentions using indexed lookup.

    Args:
        ranked_defs: List of (fname, ident) tuples with PageRank scores
        definitions: Map of (fname, ident) to set of Tags
        mentions: Set of mentioned identifiers from conversation
        ref_tokens_by_file: Reference tokens by file
        ref_names_by_file: Reference names by file
        chat_file_dirs: Directory prefixes from chat files (for context weighting)
    """
    if not ranked_defs or not mentions:
        return ranked_defs

    ref_names_by_file = ref_names_by_file or {}

    # Build inverted index once
    mention_set, prefix_index = _build_mention_index(mentions)
    mention_set_for_ref = {m.lower() for m in mentions}

    scored_defs: list[tuple[tuple[str, str], int]] = []
    for (fname, ident) in definitions:
        score = _priority_score_indexed(
            fname,
            ident,
            mention_set,
            prefix_index,
            definitions[(fname, ident)],
            ref_tokens_by_file.get(fname, set()),
            chat_file_dirs,
        )
        # Extra boost for files that have exact ref name matches
        ref_names = ref_names_by_file.get(fname, set())
        if ref_names.intersection(mention_set_for_ref):
            score += 3
        if score > 0:  # Only include positive scores
            scored_defs.append(((fname, ident), score))

    if not scored_defs:
        return ranked_defs

    has_non_tests = any(not _is_test_path(Path(fname)) for (fname, _), _ in scored_defs)
    if has_non_tests:
        scored_defs = [
            entry
            for entry in scored_defs
            if not _is_test_path(Path(entry[0][0]))
        ]

    scored_defs.sort(key=lambda item: (-item[1], item[0][0], item[0][1]))
    max_priority = min(100, len(scored_defs))
    boost_score = ranked_defs[0][1] + 1.0 if ranked_defs else 1.0

    prioritized: list[tuple[tuple[str, str], float]] = []
    seen: set[tuple[str, str]] = set()
    for (fname, ident), _ in scored_defs[:max_priority]:
        prioritized.append(((fname, ident), boost_score))
        seen.add((fname, ident))

    prioritized.extend(entry for entry in ranked_defs if entry[0] not in seen)
    return prioritized


@dataclass
class RepoMapResult:
    """Result of repo map generation with diagnostics."""
    content: str
    ranked_defs: list[tuple[tuple[str, str], float]] = field(default_factory=list)
    definitions: dict[tuple[str, str], set[Tag]] = field(default_factory=dict)
    files_processed: int = 0
    files_skipped: int = 0
    errors: list[ExtractionError] = field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0

    def status_string(self) -> str:
        """Generate a status string for display."""
        if self.errors:
            return f"Active ({len(self.errors)} errors)"
        if not self.content:
            return "Empty"
        return "Active"


@dataclass
class RepoMap:
    """RepoMap extracts and ranks the most relevant code symbols from a repository."""

    map_tokens: int = 1024
    root: str = "."
    verbose: bool = False
    cache_dir: str | None = None
    max_workers: int | None = None  # None = auto (based on CPU count)

    def get_repo_map(
        self,
        chat_files: list[str],
        other_files: list[str],
        mentioned_fnames: set[str] | None = None,
        mentioned_idents: set[str] | None = None,
        token_counter_func=None,
    ) -> str:
        """Generate the repo map string (legacy API for backward compatibility)."""
        result = self.get_repo_map_with_diagnostics(
            chat_files=chat_files,
            other_files=other_files,
            mentioned_fnames=mentioned_fnames,
            mentioned_idents=mentioned_idents,
            token_counter_func=token_counter_func,
        )
        return result.content

    def get_repo_map_with_diagnostics(
        self,
        chat_files: list[str],
        other_files: list[str],
        mentioned_fnames: set[str] | None = None,
        mentioned_idents: set[str] | None = None,
        token_counter_func=None,
    ) -> RepoMapResult:
        """Generate the repo map with full diagnostics.

        Pipeline phases:
        1. Extract tags from all files
        2. Normalize mentions and build graph
        3. Compute personalization scores
        4. Run PageRank and distribute to definitions
        5. Prioritize and render

        Returns:
            RepoMapResult containing the map content and extraction statistics.
        """
        if not other_files:
            return RepoMapResult(content="")

        mentioned_fnames = mentioned_fnames or set()
        mentioned_idents = mentioned_idents or set()

        # Phase 1: Tag Extraction
        extraction_result = self._extract_tags(chat_files, other_files)

        # Phase 2: Graph Construction
        normalized_mentions = _normalize_mentions(mentioned_idents)
        graph_result = self._build_graph(
            extraction_result.tags,
            set(chat_files),
            mentioned_fnames,
            normalized_mentions,
        )

        # Phase 3: Personalization
        all_files = list(set(chat_files + other_files))
        personalization = self._compute_personalization(
            all_files=all_files,
            chat_files=chat_files,
            mentioned_fnames=mentioned_fnames,
            tags=extraction_result.tags,
            mention_set=graph_result.mention_set,
            prefix_index=graph_result.prefix_index,
            ref_tokens_by_file=graph_result.ref_tokens_by_file,
            ref_names_by_file=graph_result.ref_names_by_file,
        )

        # Phase 4: Ranking
        ranked_defs = self._rank_definitions(
            graph=graph_result.graph,
            personalization=personalization,
            tags=extraction_result.tags,
        )

        # Phase 5: Prioritization and Rendering
        content = self._render(
            ranked_defs=ranked_defs,
            definitions=graph_result.definitions,
            normalized_mentions=normalized_mentions,
            ref_tokens_by_file=graph_result.ref_tokens_by_file,
            ref_names_by_file=graph_result.ref_names_by_file,
            chat_files=set(chat_files),
            token_counter_func=token_counter_func,
        )

        return RepoMapResult(
            content=content,
            ranked_defs=ranked_defs,
            definitions=graph_result.definitions,
            files_processed=extraction_result.files_processed,
            files_skipped=extraction_result.files_skipped,
            errors=extraction_result.errors,
            cache_hits=extraction_result.cache_hits,
            cache_misses=extraction_result.cache_misses,
        )

    def _extract_tags(
        self,
        chat_files: list[str],
        other_files: list[str],
    ) -> _ExtractionPhaseResult:
        """Phase 1: Extract tags from all files.

        Uses parallel extraction when max_workers > 1 and file count is large.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        all_tags: list[Tag] = []
        errors: list[ExtractionError] = []
        files_processed = 0
        files_skipped = 0

        if self.verbose:
            print(
                f"RepoMap: Scanning {len(other_files)} other files "
                f"and {len(chat_files)} chat files..."
            )

        all_files = list(set(chat_files + other_files))
        all_files.sort()  # Deterministic ordering

        # Prepare file list with resolved paths
        file_pairs: list[tuple[str, str]] = []
        for fname in all_files:
            if os.path.isabs(fname):
                try:
                    rel = os.path.relpath(fname, self.root)
                except ValueError:
                    rel = fname
            else:
                rel = fname
                fname = os.path.abspath(fname)
            file_pairs.append((fname, rel))

        # Decide whether to parallelize
        num_files = len(file_pairs)
        use_parallel = num_files >= 50 and self.max_workers != 1

        if use_parallel:
            # Parallel extraction with shared TagExtractor
            workers = self.max_workers or min(8, (os.cpu_count() or 4))
            # Create a single shared extractor - diskcache handles concurrency,
            # and our thread-safe counters track metrics accurately
            shared_extractor = TagExtractor(
                cache_dir=self.cache_dir,
                project_root=self.root,
            )
            results = self._extract_tags_parallel(file_pairs, workers, shared_extractor)
            for tags, error in results:
                if error:
                    errors.append(error)
                    files_skipped += 1
                else:
                    files_processed += 1
                    all_tags.extend(tags)
            cache_hits, cache_misses = shared_extractor.get_extraction_stats()
        else:
            # Sequential extraction
            extractor = TagExtractor(cache_dir=self.cache_dir, project_root=self.root)
            for fname, rel in file_pairs:
                if self.verbose:
                    print(f"RepoMap: Processing {fname}", end="\r")

                tags, error = extractor.get_tags(fname, rel)

                if error:
                    errors.append(error)
                    files_skipped += 1
                    if self.verbose:
                        print(f"RepoMap: {error}")
                else:
                    files_processed += 1
                    if self.verbose and tags:
                        print(f"  Found {len(tags)} tags in {fname}")
                    all_tags.extend(tags)

            cache_hits, cache_misses = extractor.get_extraction_stats()

        return _ExtractionPhaseResult(
            tags=all_tags,
            errors=errors,
            files_processed=files_processed,
            files_skipped=files_skipped,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
        )

    def _extract_tags_parallel(
        self,
        file_pairs: list[tuple[str, str]],
        workers: int,
        extractor: TagExtractor,
    ) -> list[tuple[list[Tag], ExtractionError | None]]:
        """Extract tags from files in parallel using ThreadPoolExecutor.

        Uses a shared TagExtractor instance for cache efficiency.
        The TagExtractor handles thread-safety internally via locking for metrics
        and diskcache for the underlying cache.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: list[tuple[list[Tag], ExtractionError | None]] = []

        def extract_single(args: tuple[str, str]) -> tuple[list[Tag], ExtractionError | None]:
            fname, rel = args
            return extractor.get_tags(fname, rel)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            # Submit all tasks
            future_to_file = {
                executor.submit(extract_single, pair): pair
                for pair in file_pairs
            }

            # Collect results in order
            for future in as_completed(future_to_file):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    fname, rel = future_to_file[future]
                    results.append(([], ExtractionError(
                        fname=fname,
                        error_type="ThreadError",
                        message=str(e),
                    )))

        return results

    def _build_graph(
        self,
        tags: list[Tag],
        chat_files: set[str],
        mentioned_fnames: set[str],
        normalized_mentions: set[str],
    ) -> _GraphPhaseResult:
        """Phase 2: Build dependency graph and extract reference maps."""
        import networkx as nx

        graph = build_graph(tags, chat_files, mentioned_fnames, normalized_mentions)

        # Build reference token maps
        ref_tokens_by_file: dict[str, set[str]] = defaultdict(set)
        ref_names_by_file: dict[str, set[str]] = defaultdict(set)
        for tag in tags:
            if tag.kind == "ref":
                ref_tokens_by_file[tag.fname].update(_extract_ident_tokens(tag.name))
                ref_names_by_file[tag.fname].add(tag.name.lower())

        # Build definitions map
        definitions: dict[tuple[str, str], set[Tag]] = defaultdict(set)
        for tag in tags:
            if tag.kind == "def":
                definitions[(tag.fname, tag.name)].add(tag)

        # Build mention index
        mention_set, prefix_index = _build_mention_index(normalized_mentions)

        return _GraphPhaseResult(
            graph=graph,
            definitions=definitions,
            ref_tokens_by_file=ref_tokens_by_file,
            ref_names_by_file=ref_names_by_file,
            mention_set=mention_set,
            prefix_index=prefix_index,
        )

    def _compute_personalization(
        self,
        all_files: list[str],
        chat_files: list[str],
        mentioned_fnames: set[str],
        tags: list[Tag],
        mention_set: set[str],
        prefix_index: dict[str, set[str]],
        ref_tokens_by_file: dict[str, set[str]],
        ref_names_by_file: dict[str, set[str]],
    ) -> dict[str, float] | None:
        """Phase 3: Compute personalization scores for PageRank."""
        personalization: dict[str, float] = {}
        base_score = 1.0 / (len(all_files) + 1)

        # Base scores with path multipliers
        for fname in all_files:
            personalization[fname] = base_score * _path_multiplier(Path(fname))

        # Chat files boost
        for f in chat_files:
            personalization[f] = base_score * 10

        # Mentioned files boost
        for f in mentioned_fnames:
            personalization[f] = base_score * 5

        # Boost files that define mentioned identifiers
        def_boosted_files: set[str] = set()
        for tag in tags:
            if tag.kind == "def" and tag.fname not in def_boosted_files:
                tag_tokens = _extract_ident_tokens(tag.name)
                tag_tokens_lower = {t.lower() for t in tag_tokens}

                if tag_tokens_lower.intersection(mention_set):
                    personalization[tag.fname] = (
                        personalization.get(tag.fname, base_score) * 20
                    )
                    def_boosted_files.add(tag.fname)
                elif _tokens_match_with_index(tag_tokens, mention_set, prefix_index):
                    personalization[tag.fname] = (
                        personalization.get(tag.fname, base_score) * 15
                    )
                    def_boosted_files.add(tag.fname)
                # NOTE: Removed substring matching (e.g., "auth" in "authenticate")
                # This caused semantic drift by matching unrelated symbols

        # Path-based boost - only boost if exact path component matches
        for fname in all_files:
            fname_parts = {p.lower() for p in Path(fname).parts}
            if fname_parts.intersection(mention_set):
                personalization[fname] = personalization.get(fname, base_score) * 10

        # Reference token boost
        for fname, ref_tokens in ref_tokens_by_file.items():
            ref_tokens_lower = {t.lower() for t in ref_tokens}
            if ref_tokens_lower.intersection(mention_set):
                personalization[fname] = personalization.get(fname, base_score) * 8

        # Exact reference name boost
        for fname, ref_names in ref_names_by_file.items():
            if ref_names.intersection(mention_set):
                personalization[fname] = personalization.get(fname, base_score) * 25

        # NOTE: Removed auth-related boosts - these were too coarse and caused
        # semantic drift (e.g., boosting ApiKeyScreen for backend auth queries)

        # Normalize
        total_p = sum(personalization.values())
        if total_p > 0:
            return {k: v / total_p for k, v in personalization.items()}
        return None

    def _rank_definitions(
        self,
        graph,
        personalization: dict[str, float] | None,
        tags: list[Tag],
    ) -> list[tuple[tuple[str, str], float]]:
        """Phase 4: Run PageRank and distribute rank to definitions."""
        ranked_files = rank_files(graph, personalization)
        return distribute_rank(ranked_files, graph)

    def _render(
        self,
        ranked_defs: list[tuple[tuple[str, str], float]],
        definitions: dict[tuple[str, str], set[Tag]],
        normalized_mentions: set[str],
        ref_tokens_by_file: dict[str, set[str]],
        ref_names_by_file: dict[str, set[str]],
        chat_files: set[str],
        token_counter_func,
    ) -> str:
        """Phase 5: Prioritize and render the final map."""
        # Extract directory context from chat files for proximity weighting
        chat_file_dirs = {str(Path(f).parent) for f in chat_files if f}

        ranked_defs = _prioritize_ranked_defs(
            ranked_defs,
            definitions,
            normalized_mentions,
            ref_tokens_by_file,
            ref_names_by_file,
            chat_file_dirs,
        )

        return render_repo_map(
            ranked_defs,
            definitions,
            self.map_tokens,
            chat_files,
            token_counter_func,
        )


@dataclass
class _ExtractionPhaseResult:
    """Internal result from extraction phase."""
    tags: list[Tag]
    errors: list[ExtractionError]
    files_processed: int
    files_skipped: int
    cache_hits: int
    cache_misses: int


@dataclass
class _GraphPhaseResult:
    """Internal result from graph building phase."""
    graph: Any  # nx.MultiDiGraph
    definitions: dict[tuple[str, str], set[Tag]]
    ref_tokens_by_file: dict[str, set[str]]
    ref_names_by_file: dict[str, set[str]]
    mention_set: set[str]
    prefix_index: dict[str, set[str]]
