from collections import defaultdict, Counter
import math
import networkx as nx
from typing import List, Dict, Set, Tuple, Any
from .tags import Tag


def _compute_idf_weight(num_definers: int, total_files: int, base: float = 1.5) -> float:
    """Compute IDF-based weight for symbol frequency damping.

    Uses logarithmic damping based on Inverse Document Frequency:
    weight = log_base(total_files / num_definers) / log_base(total_files)

    This provides smooth damping that:
    - Returns ~1.0 for symbols defined in 1 file
    - Returns ~0.5 for symbols defined in sqrt(total_files) files
    - Returns ~0.1 for symbols defined in most files

    Args:
        num_definers: Number of files that define this symbol.
        total_files: Total number of files in the repository.
        base: Logarithm base (default 1.5 for aggressive damping).

    Returns:
        Weight multiplier between 0.05 and 1.0.
    """
    if num_definers <= 1 or total_files <= 1:
        return 1.0

    # IDF formula: log(N/n) normalized by log(N)
    idf = math.log(total_files / num_definers, base) / math.log(total_files, base)

    # Clamp to reasonable range
    return max(0.05, min(1.0, idf))


def build_graph(
    tags: List[Tag],
    chat_files: Set[str],
    mentioned_fnames: Set[str],
    mentioned_idents: Set[str],
) -> nx.MultiDiGraph:
    """
    Builds the dependency graph from tags.
    """
    defines: Dict[str, Set[str]] = defaultdict(set)
    references: Dict[str, List[str]] = defaultdict(list)
    definitions: Dict[Tuple[str, str], Set[Tag]] = defaultdict(set)

    # 1. Collect Data
    for tag in tags:
        if tag.kind == "def":
            defines[tag.name].add(tag.fname)
            definitions[(tag.fname, tag.name)].add(tag)
        elif tag.kind == "ref":
            references[tag.name].append(tag.fname)

    G = nx.MultiDiGraph()

    # 2. Add files as nodes
    # Ensure all files are in the graph, even if disconnected, to prevent PageRank errors
    all_files = set(t.fname for t in tags)
    G.add_nodes_from(all_files)
    total_files = len(all_files) if all_files else 1

    # 3. Build Edges
    # A. References (Cross-file edges)
    identifiers = set(defines.keys()).intersection(set(references.keys()))

    for ident in identifiers:
        definers = defines[ident]
        ref_counts = Counter(references[ident])

        # Calculate Weight Multiplier
        weight_multiplier = 1.0

        # Refactor boost logic for reuse
        is_snake = "_" in ident and any(c.isalpha() for c in ident)
        is_kebab = "-" in ident and any(c.isalpha() for c in ident)
        is_camel = any(c.isupper() for c in ident) and any(c.islower() for c in ident)

        # Boost if mentioned in query (fuzzy match)
        for mentioned in mentioned_idents:
            mentioned_lower = mentioned.lower()
            ident_lower = ident.lower()
            if mentioned_lower in ident_lower or ident_lower in mentioned_lower:
                weight_multiplier *= 10.0
                break

        if (is_snake or is_kebab or is_camel) and len(ident) >= 8:
            weight_multiplier *= 10.0
        if ident.startswith("_"):
            weight_multiplier *= 0.1

        # IDF-based damping for common symbols (replaces naive threshold check)
        # Symbols defined in many files get logarithmically dampened
        num_definers = len(definers)
        idf_weight = _compute_idf_weight(num_definers, total_files)
        weight_multiplier *= idf_weight

        # Penalize short generic identifiers to avoid attracting too much rank to sinks
        if len(ident) <= 3 and ident.isalpha():
            weight_multiplier *= 0.1

        for referencer, count in ref_counts.items():
            for definer in definers:
                if referencer == definer:
                    continue

                final_multiplier = weight_multiplier
                if referencer in chat_files:
                    final_multiplier *= 50.0

                dampened_count = math.sqrt(count)
                edge_weight = final_multiplier * dampened_count

                G.add_edge(referencer, definer, weight=edge_weight, ident=ident)

    # B. Definitions (Self-edges)
    # Ensure every definition is represented, even if not referenced (e.g. entry points)
    for ident in defines.keys():
        definers = defines[ident]

        # Calculate Weight Multiplier (Same logic)
        weight_multiplier = 1.0
        for mentioned in mentioned_idents:
            mentioned_lower = mentioned.lower()
            ident_lower = ident.lower()
            if mentioned_lower in ident_lower or ident_lower in mentioned_lower:
                weight_multiplier *= 10.0
                break

        # Less aggressive structural boosting for self-edges to avoid noise?
        # Structural boost for self-edges: make them heavier than references
        weight_multiplier *= 100.0

        for definer in definers:
            # Self-edge represents "this file provides this feature"
            G.add_edge(definer, definer, weight=weight_multiplier, ident=ident)

    return G

def rank_files(
    G: nx.MultiDiGraph,
    personalization: Dict[str, float] | None,
) -> Dict[str, float]:
    """Apply PageRank to the graph."""
    try:
        ranked = nx.pagerank(G, weight="weight", personalization=personalization)
    except Exception:
        # Fallback if pagerank fails (e.g., empty graph)
        ranked = {node: 1.0 / len(G) for node in G.nodes()} if G else {}

    return ranked

def distribute_rank(
    ranked_files: Dict[str, float],
    G: nx.MultiDiGraph,
) -> List[Tuple[Tuple[str, str], float]]:
    """
    Distribute file ranks to (file, ident) pairs.
    """
    ranked_definitions: Dict[Tuple[str, str], float] = defaultdict(float)

    for source_file in G.nodes():
        if source_file not in ranked_files:
            continue

        source_rank = ranked_files[source_file]

        # Calculate total outgoing weight
        total_out = sum(data["weight"] for _, _, data in G.out_edges(source_file, data=True))

        if total_out == 0:
            continue

        for _, dest_file, data in G.out_edges(source_file, data=True):
            edge_rank = source_rank * data["weight"] / total_out
            ident = data["ident"]
            ranked_definitions[(dest_file, ident)] += edge_rank

    return sorted(ranked_definitions.items(), key=lambda x: x[1], reverse=True)
