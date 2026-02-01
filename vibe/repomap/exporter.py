"""Graph export utilities for dependency visualization.

Exports dependency graphs to multiple formats:
- JSON (node-link format)
- GraphML (XML-based, Gephi compatible)
- GEXF (Graph Exchange XML Format, Gephi native)

Each format includes full node/edge attributes for rich visualization.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import networkx as nx

from .schemas import DependencyGraph, GraphEdge, GraphNode, Language


def _sanitize_for_xml(value: Any) -> str:
    """Sanitize a value for XML attribute use."""
    if value is None:
        return ""
    s = str(value)
    # Replace characters that are invalid in XML
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# =============================================================================
# NetworkX Graph Construction
# =============================================================================


def build_networkx_graph(graph: DependencyGraph) -> nx.DiGraph:
    """Build a NetworkX DiGraph from DependencyGraph schema.

    Args:
        graph: DependencyGraph with nodes and edges

    Returns:
        NetworkX DiGraph with all node/edge attributes
    """
    G = nx.DiGraph()

    # Add nodes with attributes
    for node in graph.nodes:
        G.add_node(
            node.id,
            label=node.label,
            node_type=node.node_type,
            language=node.language.value if node.language else "",
            loc=node.loc,
            is_test=node.is_test,
            is_entrypoint=node.is_entrypoint,
            is_core=node.is_core,
            pagerank=node.pagerank,
            in_degree=node.in_degree,
            out_degree=node.out_degree,
            **node.metadata,
        )

    # Add edges with attributes
    for edge in graph.edges:
        G.add_edge(
            edge.source,
            edge.target,
            edge_type=edge.edge_type,
            weight=edge.weight,
            symbol=edge.symbol or "",
        )

    return G


def from_networkx_graph(G: nx.DiGraph | nx.MultiDiGraph) -> DependencyGraph:
    """Convert NetworkX graph to DependencyGraph schema.

    Args:
        G: NetworkX graph (DiGraph or MultiDiGraph)

    Returns:
        DependencyGraph with nodes and edges
    """
    nodes = []
    for node_id, attrs in G.nodes(data=True):
        lang_str = attrs.get("language", "")
        try:
            lang = Language(lang_str) if lang_str else None
        except ValueError:
            lang = None

        nodes.append(
            GraphNode(
                id=str(node_id),
                label=attrs.get("label", str(node_id)),
                node_type=attrs.get("node_type", "file"),
                language=lang,
                loc=attrs.get("loc", 0),
                is_test=attrs.get("is_test", False),
                is_entrypoint=attrs.get("is_entrypoint", False),
                is_core=attrs.get("is_core", False),
                pagerank=attrs.get("pagerank", 0.0),
                in_degree=G.in_degree(node_id),
                out_degree=G.out_degree(node_id),
            )
        )

    edges = []
    if isinstance(G, nx.MultiDiGraph):
        for source, target, key, attrs in G.edges(data=True, keys=True):
            edges.append(
                GraphEdge(
                    source=str(source),
                    target=str(target),
                    edge_type=attrs.get("edge_type", attrs.get("ident", "references")),
                    weight=attrs.get("weight", 1.0),
                    symbol=attrs.get("symbol", attrs.get("ident")),
                )
            )
    else:
        for source, target, attrs in G.edges(data=True):
            edges.append(
                GraphEdge(
                    source=str(source),
                    target=str(target),
                    edge_type=attrs.get("edge_type", "references"),
                    weight=attrs.get("weight", 1.0),
                    symbol=attrs.get("symbol"),
                )
            )

    return DependencyGraph(nodes=nodes, edges=edges)


# =============================================================================
# JSON Export
# =============================================================================


def export_json(
    graph: DependencyGraph,
    output_path: Path | None = None,
    indent: int = 2,
) -> str:
    """Export dependency graph to JSON node-link format.

    The format is compatible with D3.js and other visualization libraries:
    {
        "nodes": [{"id": "...", "label": "...", ...}, ...],
        "edges": [{"source": "...", "target": "...", ...}, ...],
        "metadata": {...}
    }

    Args:
        graph: DependencyGraph to export
        output_path: Optional path to write JSON file
        indent: JSON indentation level

    Returns:
        JSON string representation
    """
    data = {
        "nodes": [node.model_dump(mode="json") for node in graph.nodes],
        "edges": [edge.model_dump(mode="json") for edge in graph.edges],
        "metadata": {
            **graph.metadata,
            "node_count": graph.node_count,
            "edge_count": graph.edge_count,
            "exported_at": datetime.now().isoformat(),
        },
    }

    json_str = json.dumps(data, indent=indent, default=str)

    if output_path:
        output_path.write_text(json_str, encoding="utf-8")

    return json_str


def export_json_compact(graph: DependencyGraph) -> str:
    """Export dependency graph to compact JSON (no indentation).

    Useful for embedding in other documents or API responses.
    """
    data = {
        "nodes": [node.model_dump(mode="json") for node in graph.nodes],
        "edges": [edge.model_dump(mode="json") for edge in graph.edges],
        "metadata": {
            **graph.metadata,
            "node_count": graph.node_count,
            "edge_count": graph.edge_count,
            "exported_at": datetime.now().isoformat(),
        },
    }
    return json.dumps(data, default=str)


# =============================================================================
# GraphML Export
# =============================================================================


def export_graphml(
    graph: DependencyGraph,
    output_path: Path | None = None,
) -> str:
    """Export dependency graph to GraphML format.

    GraphML is an XML-based format supported by many graph tools including:
    - Gephi
    - yEd
    - Cytoscape
    - NetworkX

    Args:
        graph: DependencyGraph to export
        output_path: Optional path to write GraphML file

    Returns:
        GraphML XML string
    """
    G = build_networkx_graph(graph)

    if output_path:
        nx.write_graphml(G, str(output_path), encoding="utf-8", prettyprint=True)
        return output_path.read_text(encoding="utf-8")

    # Export to string
    from io import BytesIO

    buffer = BytesIO()
    nx.write_graphml(G, buffer, encoding="utf-8", prettyprint=True)
    return buffer.getvalue().decode("utf-8")


# =============================================================================
# GEXF Export
# =============================================================================


def export_gexf(
    graph: DependencyGraph,
    output_path: Path | None = None,
    include_viz: bool = True,
) -> str:
    """Export dependency graph to GEXF format.

    GEXF (Graph Exchange XML Format) is the native format for Gephi
    and supports rich visualization attributes.

    Args:
        graph: DependencyGraph to export
        output_path: Optional path to write GEXF file
        include_viz: Whether to include visualization hints (colors, sizes)

    Returns:
        GEXF XML string
    """
    G = build_networkx_graph(graph)

    # Add visualization attributes if requested
    if include_viz:
        _add_visualization_attributes(G, graph)

    if output_path:
        nx.write_gexf(G, str(output_path), encoding="utf-8", prettyprint=True)
        return output_path.read_text(encoding="utf-8")

    # Export to string
    from io import BytesIO

    buffer = BytesIO()
    nx.write_gexf(G, buffer, encoding="utf-8", prettyprint=True)
    return buffer.getvalue().decode("utf-8")


def _add_visualization_attributes(G: nx.DiGraph, graph: DependencyGraph) -> None:
    """Add GEXF visualization attributes to graph nodes.

    Uses 'viz' namespace for Gephi-compatible visualization:
    - size: Based on LOC
    - color: Based on language
    - position: Optional layout hints
    """
    # Color palette by language
    language_colors = {
        Language.PYTHON: {"r": 53, "g": 114, "b": 165},  # Blue
        Language.JAVASCRIPT: {"r": 247, "g": 223, "b": 30},  # Yellow
        Language.TYPESCRIPT: {"r": 49, "g": 120, "b": 198},  # TypeScript blue
        Language.GO: {"r": 0, "g": 173, "b": 216},  # Cyan
        Language.RUST: {"r": 222, "g": 165, "b": 132},  # Rust orange
        Language.JAVA: {"r": 176, "g": 114, "b": 25},  # Java orange
        Language.CPP: {"r": 0, "g": 89, "b": 157},  # C++ blue
        Language.C: {"r": 85, "g": 85, "b": 85},  # Gray
        Language.RUBY: {"r": 204, "g": 52, "b": 45},  # Ruby red
        Language.SHELL: {"r": 137, "g": 224, "b": 81},  # Green
    }
    default_color = {"r": 128, "g": 128, "b": 128}  # Gray

    for node in graph.nodes:
        if node.id not in G:
            continue

        viz = {}

        # Size based on LOC (log scale)
        import math
        loc = max(1, node.loc)
        viz["size"] = min(100, max(5, math.log10(loc + 1) * 20))

        # Color based on language
        color = language_colors.get(node.language, default_color) if node.language else default_color
        viz["color"] = color

        # Mark entrypoints and core modules with special colors
        if node.is_entrypoint:
            viz["color"] = {"r": 46, "g": 204, "b": 113}  # Green
        elif node.is_core:
            viz["color"] = {"r": 155, "g": 89, "b": 182}  # Purple
        elif node.is_test:
            viz["color"] = {"r": 189, "g": 195, "b": 199}  # Light gray

        G.nodes[node.id]["viz"] = viz


# =============================================================================
# Batch Export
# =============================================================================


def export_all_formats(
    graph: DependencyGraph,
    output_dir: Path,
    base_name: str = "dependency_graph",
) -> dict[str, Path]:
    """Export dependency graph to all supported formats.

    Args:
        graph: DependencyGraph to export
        output_dir: Directory to write files
        base_name: Base filename (without extension)

    Returns:
        Dict mapping format name to output path
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = {}

    # JSON
    json_path = output_dir / f"{base_name}.json"
    export_json(graph, json_path)
    paths["json"] = json_path

    # GraphML
    graphml_path = output_dir / f"{base_name}.graphml"
    export_graphml(graph, graphml_path)
    paths["graphml"] = graphml_path

    # GEXF
    gexf_path = output_dir / f"{base_name}.gexf"
    export_gexf(graph, gexf_path)
    paths["gexf"] = gexf_path

    return paths


# =============================================================================
# Graph Statistics
# =============================================================================


def compute_graph_statistics(graph: DependencyGraph) -> dict[str, Any]:
    """Compute various statistics about the dependency graph.

    Returns:
        Dict with statistics including centrality, clustering, components
    """
    G = build_networkx_graph(graph)

    stats: dict[str, Any] = {
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "density": nx.density(G),
    }

    # Degree statistics
    if G.number_of_nodes() > 0:
        in_degrees = [d for _, d in G.in_degree()]
        out_degrees = [d for _, d in G.out_degree()]
        stats["avg_in_degree"] = sum(in_degrees) / len(in_degrees)
        stats["avg_out_degree"] = sum(out_degrees) / len(out_degrees)
        stats["max_in_degree"] = max(in_degrees)
        stats["max_out_degree"] = max(out_degrees)

    # Weakly connected components (for directed graph)
    if G.number_of_nodes() > 0:
        components = list(nx.weakly_connected_components(G))
        stats["num_components"] = len(components)
        stats["largest_component_size"] = max(len(c) for c in components)

    # Top nodes by PageRank
    if G.number_of_nodes() > 0:
        try:
            pagerank = nx.pagerank(G, weight="weight")
            top_nodes = sorted(pagerank.items(), key=lambda x: x[1], reverse=True)[:10]
            stats["top_nodes_by_pagerank"] = [
                {"node": node, "score": score} for node, score in top_nodes
            ]
        except (nx.PowerIterationFailedConvergence, ImportError):
            stats["top_nodes_by_pagerank"] = []

    return stats
