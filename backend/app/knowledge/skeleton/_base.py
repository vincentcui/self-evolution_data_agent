"""Skeleton data model + language auto-discovery.

tree-sitter-language-pack API (verified v1.9.1):
    get_parser(name) → Parser
    parser.parse(src: str) → Tree      tree.root_node() → Node
    node.kind() → str                  node.child_count() → int
    node.child(i) → Node               node.named_child_count() → int
    node.named_child(i) → Node         node.byte_range() → ByteRange
    br.start → int (byte offset)       br.end → int (byte offset)
Text extraction: src_bytes[br.start:br.end].decode()
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.knowledge.extraction_agent import ExtractionResult

logger = logging.getLogger(__name__)


@dataclass
class LanguageConfig:
    name: str                  # "java"
    extensions: list[str]      # [".java"]
    tree_sitter_lang: str      # "java" (tree-sitter-language-pack key)
    entity_node_types: list[str]  # ["class_declaration", "enum_declaration"]


@dataclass
class Module:
    name: str              # "com/x/order"
    files: list[str]       # relative file paths
    classes: list[str]     # class/struct/enum names


@dataclass
class Skeleton:
    modules: list[Module] = field(default_factory=list)
    class_index: dict[str, str] = field(default_factory=dict)


@dataclass
class ExplorerResult:
    focus_files: list[str] = field(default_factory=list)
    focus_classes: list[str] = field(default_factory=list)
    reasoning: str = ""
    status: str = "partial"   # "ok" | "partial"


@dataclass
class WorkUnit:
    name: str

    # ── Rev 2 字段 (Explorer 输出) ──
    focus_files: list[str] = field(default_factory=list)
    focus_classes: list[str] = field(default_factory=list)
    skeleton_class_index: dict[str, str] = field(default_factory=dict)

    # ── Rev 1 字段 (DEPRECATED — 保留向后兼容) ──
    scope_dir: str = ""                                               # deprecated
    class_index_subset: dict[str, str] = field(default_factory=dict) # deprecated
    full_class_index: dict[str, str] = field(default_factory=dict)   # deprecated


@dataclass
class SubagentResult:
    """Wrapper: preserves per-subagent diagnostics through merge."""
    work_unit_name: str
    result: ExtractionResult | None  # None = subagent crashed


def merge_results(sub_results: list[SubagentResult]) -> ExtractionResult:
    from app.knowledge.extraction_agent import ExtractionResult
    all_objects, all_knowledge, reasons = [], [], []
    has_failed, has_partial = False, False
    for sr in sub_results:
        if sr.result is None:
            has_failed = True
            reasons.append(f"{sr.work_unit_name}: exception")
            continue
        all_objects.extend(sr.result.objects)
        all_knowledge.extend(sr.result.knowledge_proposals)
        if sr.result.status == "failed":
            has_failed = True
            reasons.append(f"{sr.work_unit_name}: {sr.result.reason}")
        elif sr.result.status == "partial":
            has_partial = True
            reasons.append(f"{sr.work_unit_name}: {sr.result.reason}")
    if has_failed:
        status = "failed" if not all_objects else "partial"
    elif has_partial:
        status = "partial"
    else:
        status = "ok"
    return ExtractionResult(objects=all_objects, knowledge_proposals=all_knowledge,
                            status=status, reason="; ".join(reasons))


# ── Language auto-discovery ──
_LANGUAGES_DIR = Path(__file__).parent / "languages"


def _discover_language_configs() -> dict[str, LanguageConfig]:
    from tree_sitter_language_pack import DownloadError, get_parser
    configs: dict[str, LanguageConfig] = {}
    if not _LANGUAGES_DIR.is_dir():
        return configs
    for entry in sorted(os.listdir(_LANGUAGES_DIR)):
        lang_dir = _LANGUAGES_DIR / entry
        if not lang_dir.is_dir():
            continue
        cfg_path = lang_dir / "config.json"
        if not cfg_path.is_file():
            continue
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg = LanguageConfig(name=raw["name"], extensions=raw["extensions"],
                tree_sitter_lang=raw["tree_sitter_lang"],
                entity_node_types=raw.get("entity_node_types", []))
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning("skip language %s: config invalid (%s)", entry, e)
            continue
        try:
            get_parser(cfg.tree_sitter_lang)  # verify grammar installed
            configs[cfg.name] = cfg
        except (ImportError, LookupError, AttributeError, RuntimeError, DownloadError):
            logger.warning("skip language %s: grammar not installed/downloadable", entry)
    return configs


LANGUAGE_CONFIGS: dict[str, LanguageConfig] = _discover_language_configs()
