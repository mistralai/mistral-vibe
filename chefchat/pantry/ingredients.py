"""ChefChat Ingredients Manager - Knowledge Graph & AST Parser.

The Pantry's ingredient inventory - a knowledge graph of the codebase.
Uses Python's AST module to parse code structure and NetworkX to build
a graph of relationships between files, classes, functions, and imports.

"Mise en place" - everything in its place, ready to cook.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from enum import Enum, auto
import json
from pathlib import Path
import pickle
from typing import Any

import networkx as nx


class NodeType(Enum):
    """Types of nodes in the knowledge graph."""

    FILE = auto()
    CLASS = auto()
    FUNCTION = auto()
    METHOD = auto()
    IMPORT = auto()
    VARIABLE = auto()


class EdgeType(Enum):
    """Types of edges (relationships) in the knowledge graph."""

    DEFINES = "defines"  # File defines Class/Function
    CONTAINS = "contains"  # Class contains Method
    IMPORTS = "imports"  # File imports Module
    CALLS = "calls"  # Function calls another
    INHERITS = "inherits"  # Class inherits from another


@dataclass
class CodeNode:
    """A node in the knowledge graph representing a code entity."""

    id: str
    name: str
    node_type: NodeType
    file_path: str
    line_start: int = 0
    line_end: int = 0
    docstring: str | None = None
    signature: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "node_type": self.node_type.name,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "docstring": self.docstring,
            "signature": self.signature,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeNode:
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            node_type=NodeType[data["node_type"]],
            file_path=data["file_path"],
            line_start=data.get("line_start", 0),
            line_end=data.get("line_end", 0),
            docstring=data.get("docstring"),
            signature=data.get("signature"),
            metadata=data.get("metadata", {}),
        )


class ASTVisitor(ast.NodeVisitor):
    """AST Visitor that extracts code structure information."""

    def __init__(self, file_path: str) -> None:
        """Initialize the visitor.

        Args:
            file_path: Path to the file being parsed
        """
        self.file_path = file_path
        self.nodes: list[CodeNode] = []
        self.edges: list[tuple[str, str, EdgeType]] = []
        self._current_class: str | None = None
        self._file_id = f"file:{file_path}"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Visit a class definition."""
        class_id = f"class:{self.file_path}:{node.name}"

        # Get docstring
        docstring = ast.get_docstring(node)

        # Get base classes for inheritance
        bases = [self._get_name(base) for base in node.bases]

        code_node = CodeNode(
            id=class_id,
            name=node.name,
            node_type=NodeType.CLASS,
            file_path=self.file_path,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            docstring=docstring,
            metadata={"bases": bases},
        )
        self.nodes.append(code_node)

        # Edge: File defines Class
        self.edges.append((self._file_id, class_id, EdgeType.DEFINES))

        # Edges: Class inherits from bases
        for base in bases:
            if base:
                self.edges.append((class_id, f"ref:{base}", EdgeType.INHERITS))

        # Visit methods within this class
        old_class = self._current_class
        self._current_class = class_id
        self.generic_visit(node)
        self._current_class = old_class

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Visit a function/method definition."""
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Visit an async function/method definition."""
        self._visit_function(node, is_async=True)

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool = False
    ) -> None:
        """Common logic for visiting functions."""
        if self._current_class:
            # It's a method
            func_id = f"method:{self.file_path}:{self._current_class.split(':')[-1]}.{node.name}"
            node_type = NodeType.METHOD
            parent_id = self._current_class
            edge_type = EdgeType.CONTAINS
        else:
            # It's a top-level function
            func_id = f"function:{self.file_path}:{node.name}"
            node_type = NodeType.FUNCTION
            parent_id = self._file_id
            edge_type = EdgeType.DEFINES

        # Get docstring and signature
        docstring = ast.get_docstring(node)
        signature = self._get_function_signature(node, is_async)

        code_node = CodeNode(
            id=func_id,
            name=node.name,
            node_type=node_type,
            file_path=self.file_path,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            docstring=docstring,
            signature=signature,
            metadata={"is_async": is_async},
        )
        self.nodes.append(code_node)

        # Edge: Parent defines/contains this function
        self.edges.append((parent_id, func_id, edge_type))

        # Don't recurse into nested functions for now

    def visit_Import(self, node: ast.Import) -> None:
        """Visit an import statement."""
        for alias in node.names:
            import_id = f"import:{alias.name}"
            self.edges.append((self._file_id, import_id, EdgeType.IMPORTS))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Visit a from...import statement."""
        module = node.module or ""
        for alias in node.names:
            import_name = f"{module}.{alias.name}" if module else alias.name
            import_id = f"import:{import_name}"
            self.edges.append((self._file_id, import_id, EdgeType.IMPORTS))

    def _get_name(self, node: ast.expr) -> str:
        """Get the name from an expression node."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{self._get_name(node.value)}.{node.attr}"
        return ""

    def _get_function_signature(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool
    ) -> str:
        """Generate a function signature string."""
        args = []

        # Regular arguments
        for arg in node.args.args:
            arg_str = arg.arg
            if arg.annotation:
                arg_str += f": {ast.unparse(arg.annotation)}"
            args.append(arg_str)

        # *args
        if node.args.vararg:
            args.append(f"*{node.args.vararg.arg}")

        # **kwargs
        if node.args.kwarg:
            args.append(f"**{node.args.kwarg.arg}")

        signature = f"{'async ' if is_async else ''}def {node.name}({', '.join(args)})"

        # Return annotation
        if node.returns:
            signature += f" -> {ast.unparse(node.returns)}"

        return signature


class IngredientsManager:
    """Manages the knowledge graph of the codebase.

    "Know your ingredients" - a good chef knows exactly what's
    in their pantry and how everything relates.
    """

    def __init__(self, root_path: str | Path) -> None:
        """Initialize the ingredients manager.

        Args:
            root_path: Root directory of the project to scan
        """
        self.root_path = Path(root_path)
        self.graph = nx.DiGraph()
        self._nodes: dict[str, CodeNode] = {}
        self._scan_complete = False

    def scan(self, exclude_patterns: list[str] | None = None) -> dict[str, int]:
        """Scan the codebase and build the knowledge graph.

        Args:
            exclude_patterns: Glob patterns to exclude (e.g., ['**/test*'])

        Returns:
            Statistics about what was scanned
        """
        if exclude_patterns is None:
            exclude_patterns = [
                "**/__pycache__/**",
                "**/.git/**",
                "**/.venv/**",
                "**/venv/**",
                "**/node_modules/**",
                "**/*.pyc",
            ]

        stats = {
            "files": 0,
            "classes": 0,
            "functions": 0,
            "methods": 0,
            "imports": 0,
            "edges": 0,
        }

        # Find all Python files
        py_files = list(self.root_path.rglob("*.py"))

        for py_file in py_files:
            # Check exclusions
            rel_path = str(py_file.relative_to(self.root_path))
            if any(py_file.match(pattern) for pattern in exclude_patterns):
                continue

            try:
                self._parse_file(py_file, stats)
            except SyntaxError:
                # Skip files with syntax errors
                continue
            except Exception:
                # Skip files that can't be parsed
                continue

        self._scan_complete = True
        stats["edges"] = self.graph.number_of_edges()

        return stats

    def _parse_file(self, file_path: Path, stats: dict[str, int]) -> None:
        """Parse a single Python file.

        Args:
            file_path: Path to the file
            stats: Statistics dict to update
        """
        rel_path = str(file_path.relative_to(self.root_path))

        # Read and parse the file
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Create file node
        file_id = f"file:{rel_path}"
        file_node = CodeNode(
            id=file_id,
            name=file_path.name,
            node_type=NodeType.FILE,
            file_path=rel_path,
            line_start=1,
            line_end=len(source.splitlines()),
            docstring=ast.get_docstring(tree),
            metadata={"size_bytes": len(source)},
        )
        self._add_node(file_node)
        stats["files"] += 1

        # Visit AST
        visitor = ASTVisitor(rel_path)
        visitor.visit(tree)

        # Add nodes and edges
        for node in visitor.nodes:
            self._add_node(node)
            if node.node_type == NodeType.CLASS:
                stats["classes"] += 1
            elif node.node_type == NodeType.FUNCTION:
                stats["functions"] += 1
            elif node.node_type == NodeType.METHOD:
                stats["methods"] += 1

        for source_id, target_id, edge_type in visitor.edges:
            self.graph.add_edge(source_id, target_id, type=edge_type.value)
            if edge_type == EdgeType.IMPORTS:
                stats["imports"] += 1

    def _add_node(self, node: CodeNode) -> None:
        """Add a node to the graph.

        Args:
            node: The CodeNode to add
        """
        self._nodes[node.id] = node
        self.graph.add_node(node.id, **node.to_dict())

    def get_node(self, node_id: str) -> CodeNode | None:
        """Get a node by ID.

        Args:
            node_id: The node ID

        Returns:
            The CodeNode if found
        """
        return self._nodes.get(node_id)

    def find_by_name(self, name: str) -> list[CodeNode]:
        """Find nodes by name.

        Args:
            name: Name to search for

        Returns:
            List of matching nodes
        """
        return [n for n in self._nodes.values() if n.name == name]

    def find_by_type(self, node_type: NodeType) -> list[CodeNode]:
        """Find all nodes of a given type.

        Args:
            node_type: Type of nodes to find

        Returns:
            List of matching nodes
        """
        return [n for n in self._nodes.values() if n.node_type == node_type]

    def get_dependencies(self, node_id: str) -> list[str]:
        """Get what a node depends on (imports, inherits, calls).

        Args:
            node_id: The node to check

        Returns:
            List of node IDs this node depends on
        """
        return list(self.graph.successors(node_id))

    def get_dependents(self, node_id: str) -> list[str]:
        """Get what depends on a node.

        Args:
            node_id: The node to check

        Returns:
            List of node IDs that depend on this node
        """
        return list(self.graph.predecessors(node_id))

    def save(self, path: str | Path, format: str = "json") -> None:
        """Save the knowledge graph to disk.

        Args:
            path: Path to save to
            format: Format ('json' or 'pickle')
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if format == "json":
            data = {
                "nodes": {k: v.to_dict() for k, v in self._nodes.items()},
                "edges": [
                    {"source": u, "target": v, "type": d.get("type", "")}
                    for u, v, d in self.graph.edges(data=True)
                ],
                "root_path": str(self.root_path),
            }
            path.write_text(json.dumps(data, indent=2))
        else:
            with open(path, "wb") as f:
                pickle.dump(
                    {
                        "nodes": self._nodes,
                        "graph": self.graph,
                        "root_path": self.root_path,
                    },
                    f,
                )

    def load(self, path: str | Path) -> None:
        """Load a knowledge graph from disk.

        Args:
            path: Path to load from
        """
        path = Path(path)

        if path.suffix == ".json":
            data = json.loads(path.read_text())
            self._nodes = {k: CodeNode.from_dict(v) for k, v in data["nodes"].items()}
            self.graph = nx.DiGraph()
            for node_id, node in self._nodes.items():
                self.graph.add_node(node_id, **node.to_dict())
            for edge in data["edges"]:
                self.graph.add_edge(edge["source"], edge["target"], type=edge["type"])
            self.root_path = Path(data["root_path"])
        else:
            with open(path, "rb") as f:
                data = pickle.load(f)
                self._nodes = data["nodes"]
                self.graph = data["graph"]
                self.root_path = data["root_path"]

        self._scan_complete = True

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of the knowledge graph.

        Returns:
            Dict with summary statistics
        """
        return {
            "root_path": str(self.root_path),
            "scan_complete": self._scan_complete,
            "total_nodes": len(self._nodes),
            "total_edges": self.graph.number_of_edges(),
            "by_type": {nt.name: len(self.find_by_type(nt)) for nt in NodeType},
        }


# Convenience function for creating and scanning
def scan_codebase(root_path: str | Path) -> IngredientsManager:
    """Scan a codebase and return the ingredients manager.

    Args:
        root_path: Root of the project to scan

    Returns:
        IngredientsManager with the scanned graph
    """
    manager = IngredientsManager(root_path)
    manager.scan()
    return manager
