"""Thin adapter over tree-sitter-language-pack — isolates API surface.

All tree-sitter calls go through this module. When tree-sitter-language-pack
API changes, only this file needs updating — scanner.py doesn't touch it directly.
"""
from __future__ import annotations

from tree_sitter_language_pack import get_parser

__all__ = ["parse_file", "iter_nodes", "node_kind", "node_text", "node_named_children"]


def parse_file(file_path: str, lang_key: str):
    """Parse a source file → (root_node, src_bytes). Returns (None, None) on failure."""
    parser = get_parser(lang_key)
    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            src = f.read()
    except (OSError, PermissionError):
        return None, None
    try:
        tree = parser.parse(src)
    except Exception:
        return None, None
    if tree is None:
        return None, None
    return tree.root_node(), src.encode("utf-8")


def iter_nodes(root):
    """Iterative DFS over all AST nodes (stack-based, no recursion)."""
    stack: list = [root]
    while stack:
        node = stack.pop()
        yield node
        for i in range(node.child_count() - 1, -1, -1):
            stack.append(node.child(i))


def node_kind(node) -> str:
    return node.kind()


def node_text(node, src_bytes: bytes) -> str:
    br = node.byte_range()
    return src_bytes[br.start:br.end].decode()


def node_named_children(node):
    """Yield (node_kind_str, node) for each named child."""
    for i in range(node.named_child_count()):
        child = node.named_child(i)
        yield child.kind(), child  # kind() is a method, NOT a property
