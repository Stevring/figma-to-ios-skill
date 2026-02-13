#!/usr/bin/env python3
"""Read Figma JSON skeleton and node details for markdown-first mapping workflows."""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path
from typing import Any


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def as_num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compact(text: Any, max_len: int = 56) -> str:
    value = as_str(text)
    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "…"


def normalize_name(name: str) -> str:
    return " ".join(name.split())


def unique_path(path: str, seen_paths: dict[str, int]) -> str:
    count = seen_paths.get(path, 0)
    seen_paths[path] = count + 1
    if count == 0:
        return path
    return f"{path}#{count + 1}"


def get_children(node: dict[str, Any]) -> list[dict[str, Any]]:
    children = node.get("children")
    if not isinstance(children, list):
        return []
    return [child for child in children if isinstance(child, dict)]


def resolve_node_id(
    raw_node: dict[str, Any],
    parent_node_id: str | None,
    child_index: int,
    seen_ids: dict[str, int],
) -> str:
    raw_id = raw_node.get("id")
    base_id = as_str(raw_id).strip()
    if not base_id:
        prefix = parent_node_id if parent_node_id else "root"
        base_id = f"{prefix}::child-{child_index}"

    count = seen_ids.get(base_id, 0)
    seen_ids[base_id] = count + 1
    if count == 0:
        return base_id
    return f"{base_id}#{count + 1}"


def build_index(input_path: str) -> dict[str, Any]:
    root = load_json(input_path)
    if not isinstance(root, dict):
        raise ValueError("Input JSON root must be an object.")

    seen_ids: dict[str, int] = {}
    records: dict[str, dict[str, Any]] = {}
    bfs_ids: list[str] = []
    path_to_id: dict[str, str] = {}
    children_map: dict[str, list[str]] = {}
    seen_paths: dict[str, int] = {}

    root_name = normalize_name(as_str(root.get("name"), "Root"))
    queue = deque([(root, None, 0, root_name, 0)])

    while queue:
        raw_node, parent_id, depth, raw_path, child_index = queue.popleft()
        path = unique_path(raw_path, seen_paths)
        node_id = resolve_node_id(raw_node, parent_id, child_index, seen_ids)
        node_name = normalize_name(as_str(raw_node.get("name"), node_id))
        node_type = as_str(raw_node.get("type"), "UNKNOWN")
        children = get_children(raw_node)

        if parent_id is not None:
            children_map.setdefault(parent_id, []).append(node_id)

        record = {
            "bfs_index": len(bfs_ids),
            "node_id": node_id,
            "node_name": node_name,
            "figma_type": node_type,
            "parent_node_id": parent_id,
            "depth": depth,
            "path": path,
            "child_node_ids": [],
            "child_count": 0,
            "raw": raw_node,
        }
        records[node_id] = record
        bfs_ids.append(node_id)
        path_to_id[path] = node_id

        for idx, child in enumerate(children):
            child_name = normalize_name(as_str(child.get("name"), f"child-{idx}"))
            child_path = f"{path}/{child_name}"
            queue.append((child, node_id, depth + 1, child_path, idx))

    for node_id in bfs_ids:
        child_ids = children_map.get(node_id, [])
        child_ids.sort(key=lambda cid: records[cid]["bfs_index"])
        records[node_id]["child_node_ids"] = child_ids
        records[node_id]["child_count"] = len(child_ids)

    root_id = bfs_ids[0] if bfs_ids else ""
    return {
        "source_file": str(Path(input_path).resolve()),
        "root_node_id": root_id,
        "total_nodes": len(bfs_ids),
        "records": records,
        "bfs_ids": bfs_ids,
        "path_to_id": path_to_id,
    }


def summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "bfs_index": record.get("bfs_index"),
        "node_id": record.get("node_id"),
        "node_name": record.get("node_name"),
        "figma_type": record.get("figma_type"),
        "parent_node_id": record.get("parent_node_id"),
        "depth": record.get("depth"),
        "path": record.get("path"),
        "child_count": record.get("child_count"),
    }


def extract_node_reference(raw_node: dict[str, Any]) -> dict[str, Any]:
    reference = {
        "frame": {
            "x": as_num(raw_node.get("x")),
            "y": as_num(raw_node.get("y")),
            "width": as_num(raw_node.get("width")),
            "height": as_num(raw_node.get("height")),
        },
        "layout": {
            "layoutMode": raw_node.get("layoutMode"),
            "layoutPositioning": raw_node.get("layoutPositioning"),
            "layoutSizingHorizontal": raw_node.get("layoutSizingHorizontal"),
            "layoutSizingVertical": raw_node.get("layoutSizingVertical"),
            "layoutAlign": raw_node.get("layoutAlign"),
            "layoutGrow": raw_node.get("layoutGrow"),
            "itemSpacing": raw_node.get("itemSpacing"),
            "paddingLeft": raw_node.get("paddingLeft"),
            "paddingRight": raw_node.get("paddingRight"),
            "paddingTop": raw_node.get("paddingTop"),
            "paddingBottom": raw_node.get("paddingBottom"),
            "constraints": raw_node.get("constraints"),
        },
        "style": {
            "fills": raw_node.get("fills"),
            "strokes": raw_node.get("strokes"),
            "strokeWeight": raw_node.get("strokeWeight"),
            "cornerRadius": raw_node.get("cornerRadius"),
            "topLeftRadius": raw_node.get("topLeftRadius"),
            "topRightRadius": raw_node.get("topRightRadius"),
            "bottomLeftRadius": raw_node.get("bottomLeftRadius"),
            "bottomRightRadius": raw_node.get("bottomRightRadius"),
            "effects": raw_node.get("effects"),
            "opacity": raw_node.get("opacity"),
        },
        "text": {
            "characters": raw_node.get("characters"),
            "textAutoResize": raw_node.get("textAutoResize"),
            "textAlignHorizontal": raw_node.get("textAlignHorizontal"),
            "textAlignVertical": raw_node.get("textAlignVertical"),
            "fontName": raw_node.get("fontName"),
            "fontSize": raw_node.get("fontSize"),
            "textDecoration": raw_node.get("textDecoration"),
            "textVariableName": raw_node.get("textVariableName"),
        },
    }
    return reference


def to_markdown_row(cols: list[str]) -> str:
    escaped = [as_str(c).replace("|", "\\|") for c in cols]
    return "| " + " | ".join(escaped) + " |"


def render_tree_lines(
    node_id: str,
    records: dict[str, dict[str, Any]],
    depth: int,
    max_depth: int | None,
    lines: list[str],
) -> None:
    if node_id not in records:
        return
    if max_depth is not None and depth > max_depth:
        return

    node = records[node_id]
    indent = "  " * depth
    lines.append(
        f"{indent}- [{node['bfs_index']}] {node['node_name']} (`{node['node_id']}`) <{node['figma_type']}>"
    )

    if max_depth is not None and depth == max_depth and node["child_count"] > 0:
        lines.append(f"{indent}  - ...")
        return

    for child_id in node["child_node_ids"]:
        render_tree_lines(child_id, records, depth + 1, max_depth, lines)


def render_skeleton_markdown(index: dict[str, Any], max_depth: int | None, limit: int | None) -> str:
    records = index["records"]
    bfs_ids = index["bfs_ids"]
    root_id = index["root_node_id"]

    lines: list[str] = []
    lines.append("# JSON Skeleton")
    lines.append("")
    lines.append(f"- Source: `{index['source_file']}`")
    lines.append(f"- Root node id: `{root_id}`")
    lines.append(f"- Total nodes: `{index['total_nodes']}`")
    lines.append("")
    lines.append("## Tree Structure")
    lines.append("")
    render_tree_lines(root_id, records, 0, max_depth, lines)
    lines.append("")
    lines.append("## BFS Index")
    lines.append("")
    lines.append(to_markdown_row(["BFS", "Node ID", "Node Name", "Type", "Parent", "Path"]))
    lines.append(to_markdown_row(["---", "---", "---", "---", "---", "---"]))

    shown_ids = bfs_ids
    if limit is not None:
        shown_ids = bfs_ids[:limit]

    for node_id in shown_ids:
        node = records[node_id]
        lines.append(
            to_markdown_row(
                [
                    str(node["bfs_index"]),
                    node["node_id"],
                    compact(node["node_name"], 40),
                    node["figma_type"],
                    as_str(node["parent_node_id"], "ROOT"),
                    compact(node["path"], 60),
                ]
            )
        )

    if limit is not None and limit < len(bfs_ids):
        lines.append("")
        lines.append(f"_Showing first {limit} / {len(bfs_ids)} nodes._")

    return "\n".join(lines)


def select_record(
    index: dict[str, Any],
    node_id: str | None,
    bfs_index: int | None,
    node_path: str | None,
) -> dict[str, Any]:
    records = index["records"]
    bfs_ids = index["bfs_ids"]
    path_to_id = index["path_to_id"]

    if node_id is not None:
        if node_id not in records:
            raise KeyError(f"Node id not found: {node_id}")
        return records[node_id]

    if bfs_index is not None:
        if bfs_index < 0 or bfs_index >= len(bfs_ids):
            raise IndexError(f"BFS index out of range: {bfs_index}")
        return records[bfs_ids[bfs_index]]

    if node_path is not None:
        if node_path not in path_to_id:
            raise KeyError(f"Node path not found: {node_path}")
        return records[path_to_id[node_path]]

    raise ValueError("One selector is required: --node-id, --bfs-index, or --node-path")


def render_node_markdown(payload: dict[str, Any]) -> str:
    node = payload["node"]
    parent = payload["parent"]
    children = payload["children"]
    reference = payload["reference"]

    lines: list[str] = []
    lines.append(f"# Node Context: {node['node_name']} (`{node['node_id']}`)")
    lines.append("")
    lines.append("## Node Summary")
    lines.append("")
    lines.append(f"- BFS index: `{node['bfs_index']}`")
    lines.append(f"- Figma type: `{node['figma_type']}`")
    lines.append(f"- Parent: `{node['parent_node_id'] or 'ROOT'}`")
    lines.append(f"- Path: `{node['path']}`")
    lines.append(f"- Child count: `{node['child_count']}`")
    lines.append("")

    lines.append("## Parent Summary")
    lines.append("")
    if parent is None:
        lines.append("- `ROOT` node (no parent).")
    else:
        lines.append(
            f"- [{parent['bfs_index']}] {parent['node_name']} (`{parent['node_id']}`) <{parent['figma_type']}>"
        )
    lines.append("")

    lines.append("## Direct Children")
    lines.append("")
    if not children:
        lines.append("- No direct children.")
    else:
        lines.append(to_markdown_row(["BFS", "Node ID", "Node Name", "Type", "Path"]))
        lines.append(to_markdown_row(["---", "---", "---", "---", "---"]))
        for child in children:
            lines.append(
                to_markdown_row(
                    [
                        str(child["bfs_index"]),
                        child["node_id"],
                        compact(child["node_name"], 36),
                        child["figma_type"],
                        compact(child["path"], 56),
                    ]
                )
            )
    lines.append("")

    lines.append("## Reference Properties")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(reference, ensure_ascii=False, indent=2))
    lines.append("```")
    return "\n".join(lines)


def render_batch_markdown(items: list[dict[str, Any]], total_nodes: int, start: int, count: int) -> str:
    lines: list[str] = []
    lines.append("# BFS Batch")
    lines.append("")
    lines.append(f"- Start index: `{start}`")
    lines.append(f"- Requested count: `{count}`")
    lines.append(f"- Returned count: `{len(items)}`")
    lines.append(f"- Total nodes: `{total_nodes}`")
    lines.append("")
    lines.append(to_markdown_row(["BFS", "Node ID", "Node Name", "Type", "Parent", "Path"]))
    lines.append(to_markdown_row(["---", "---", "---", "---", "---", "---"]))
    for node in items:
        lines.append(
            to_markdown_row(
                [
                    str(node["bfs_index"]),
                    node["node_id"],
                    compact(node["node_name"], 40),
                    node["figma_type"],
                    as_str(node["parent_node_id"], "ROOT"),
                    compact(node["path"], 60),
                ]
            )
        )
    return "\n".join(lines)


def emit_output(payload: Any, output_path: str | None, fmt: str) -> None:
    if fmt == "json":
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        text = as_str(payload)

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")
        print(f"Wrote output -> {out.resolve()}")
    else:
        print(text)


def cmd_skeleton(args: argparse.Namespace) -> int:
    index = build_index(args.input)
    records = index["records"]
    bfs_ids = index["bfs_ids"]

    if args.format == "json":
        payload = {
            "source_file": index["source_file"],
            "root_node_id": index["root_node_id"],
            "total_nodes": index["total_nodes"],
            "nodes": [summary(records[node_id]) for node_id in bfs_ids],
        }
        emit_output(payload, args.output, "json")
        return 0

    markdown = render_skeleton_markdown(index, args.max_depth, args.limit)
    emit_output(markdown, args.output, "markdown")
    return 0


def cmd_node(args: argparse.Namespace) -> int:
    index = build_index(args.input)
    records = index["records"]

    node = select_record(index, args.node_id, args.bfs_index, args.node_path)
    parent = records.get(node["parent_node_id"]) if node.get("parent_node_id") else None
    children = [records[child_id] for child_id in node["child_node_ids"] if child_id in records]

    payload = {
        "node": summary(node),
        "parent": summary(parent) if parent else None,
        "children": [summary(child) for child in children],
        "reference": extract_node_reference(node["raw"]),
    }

    if args.raw:
        payload["raw"] = node["raw"]

    if args.format == "json":
        emit_output(payload, args.output, "json")
        return 0

    markdown = render_node_markdown(payload)
    emit_output(markdown, args.output, "markdown")
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    index = build_index(args.input)
    records = index["records"]
    bfs_ids = index["bfs_ids"]

    start = max(0, args.start)
    end = max(start, start + max(0, args.count))
    selected_ids = bfs_ids[start:end]
    items = [summary(records[node_id]) for node_id in selected_ids]

    if args.format == "json":
        payload = {
            "source_file": index["source_file"],
            "start": start,
            "count": args.count,
            "returned": len(items),
            "total_nodes": index["total_nodes"],
            "items": items,
        }
        emit_output(payload, args.output, "json")
        return 0

    markdown = render_batch_markdown(items, index["total_nodes"], start, args.count)
    emit_output(markdown, args.output, "markdown")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read Figma JSON skeleton and node details for mapping workflows."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_skeleton = sub.add_parser("skeleton", help="Read full tree skeleton for global context.")
    p_skeleton.add_argument("--input", required=True, help="Path to Figma JSON file.")
    p_skeleton.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p_skeleton.add_argument("--output", help="Optional output path.")
    p_skeleton.add_argument("--max-depth", type=int, help="Optional max depth for tree rendering.")
    p_skeleton.add_argument("--limit", type=int, help="Optional BFS row limit for markdown/json list.")
    p_skeleton.set_defaults(func=cmd_skeleton)

    p_node = sub.add_parser("node", help="Read details for one node.")
    p_node.add_argument("--input", required=True, help="Path to Figma JSON file.")
    selector = p_node.add_mutually_exclusive_group(required=True)
    selector.add_argument("--node-id", help="Target node id.")
    selector.add_argument("--bfs-index", type=int, help="Target node by BFS index.")
    selector.add_argument("--node-path", help="Target node by full path.")
    p_node.add_argument("--raw", action="store_true", help="Include full raw node in JSON output.")
    p_node.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p_node.add_argument("--output", help="Optional output path.")
    p_node.set_defaults(func=cmd_node)

    p_batch = sub.add_parser("batch", help="Read BFS batch of nodes.")
    p_batch.add_argument("--input", required=True, help="Path to Figma JSON file.")
    p_batch.add_argument("--start", type=int, default=0, help="BFS start index.")
    p_batch.add_argument("--count", type=int, default=10, help="Number of nodes to return.")
    p_batch.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p_batch.add_argument("--output", help="Optional output path.")
    p_batch.set_defaults(func=cmd_batch)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except (ValueError, KeyError, IndexError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
