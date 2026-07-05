"""Skeleton scanner — tree-sitter AST walk → class_index + module tree."""
from __future__ import annotations

import logging
from pathlib import Path

from langfuse import observe

from app.knowledge.skeleton._base import LANGUAGE_CONFIGS, Module, Skeleton
from app.tracing import get_client as _lf_client

logger = logging.getLogger(__name__)

_NAME_KINDS: frozenset[str] = frozenset({
    "identifier", "type_identifier", "constant", "simple_identifier",
})


def _extract_class_names_from_file(file_path: Path, cfg) -> list[str]:
    from app.knowledge.skeleton._ts import (
        iter_nodes,
        node_kind,
        node_named_children,
        node_text,
        parse_file,
    )
    root, src_bytes = parse_file(str(file_path), cfg.tree_sitter_lang)
    if root is None or src_bytes is None:
        return []
    entity_types = set(cfg.entity_node_types)
    names: list[str] = []
    for node in iter_nodes(root):
        if node_kind(node) in entity_types:
            for kind, child in node_named_children(node):
                if kind in _NAME_KINDS:
                    names.append(node_text(child, src_bytes))
                    break
    return names


def _file_extensions() -> set[str]:
    exts: set[str] = set()
    for cfg in LANGUAGE_CONFIGS.values():
        exts.update(cfg.extensions)
    return exts


# Directories to skip during rglob — avoid traversing dependency/vendor/build dirs
_SKIP_DIRS: frozenset[str] = frozenset({
    "node_modules", ".git", "__pycache__", "target", "build", "dist",
    ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "vendor", "bower_components",
})


@observe(name="scan_skeleton", as_type="span", capture_output=False)
def scan_skeleton(repo_path: str) -> Skeleton:
    root = Path(repo_path).resolve()
    exts = _file_extensions()
    dir_files: dict[str, list[str]] = {}
    class_index: dict[str, str] = {}
    for fp in root.rglob("*"):
        if not fp.is_file():
            continue
        if any(skip in fp.parts for skip in _SKIP_DIRS):
            continue
        if fp.suffix.lower() not in exts:
            continue
        rel = str(fp.relative_to(root))
        parent_dir = str(fp.parent.relative_to(root))
        dir_files.setdefault(parent_dir, []).append(rel)
        for cfg in LANGUAGE_CONFIGS.values():
            if fp.suffix.lower() in cfg.extensions:
                # First-match policy: .h → C (cfg "c" matched before "cpp" whose
                # extensions are [.cpp,.cc,.cxx,.hpp,.hh,.hxx] — .h not included)
                for cls_name in _extract_class_names_from_file(fp, cfg):
                    class_index[cls_name] = rel
                break
    modules: list[Module] = []
    for dir_path, files in sorted(dir_files.items()):
        dir_classes = [c for c, f in class_index.items() if f in files]
        modules.append(Module(name=dir_path if dir_path != "." else "(root)",
                              files=sorted(files), classes=sorted(dir_classes)))
    sk = Skeleton(modules=modules, class_index=class_index)

    # ── langfuse 手动提交 (小 payload, 防 timeout) ──
    lf = _lf_client()
    if lf is not None:
        try:
            lf.start_observation(
                name="scan_skeleton.result",
                as_type="span",
                input={"repo_path": repo_path},
                output={
                    "class_count": len(class_index),
                    "module_count": len(modules),
                    "languages_detected": sorted({
                        cfg.name for cfg in LANGUAGE_CONFIGS.values()
                    }),
                },
            ).end()
        except Exception:
            pass  # langfuse 故障不影响主业务

    return sk
