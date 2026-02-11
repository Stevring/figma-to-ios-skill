#!/usr/bin/env python3
"""
Figma -> iOS spec helper (BFS / parent-first traversal).

This tool is meant to solve *context limits*:
- It indexes a Figma node JSON into a compact, queryable state file.
- It exposes small queries: skeleton / children / facts.
- It supports BFS iteration: "next" returns the next undecided node with parent context.
- It stores LLM decisions ("apply") and exports the final parent-child spec tree ("export").

All outputs are JSON on stdout. Errors/warnings go to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union


STATE_VERSION = 1
DEFAULT_STATE_PATH = ".ai_tmp/figma_ios_state.json"
UI_SYSTEMS = ("UIKit", "SwiftUI")

# Pins string grammar (layout constraints)
#
# Format: "pins=K=V:K=V:..."
# Keys:
#   L  leading   (to parent.leading)  constant in points
#   R  trailing  (to parent.trailing) constant in points (often negative for inset)
#   T  top       (to parent.top)
#   B  bottom    (to parent.bottom)   (often negative for inset)
#   CX centerX   (to parent.centerX)
#   CY centerY   (to parent.centerY)
#   W  width     (constant)
#   H  height    (constant)
PINS_ALLOWED_KEYS = {"L", "R", "T", "B", "CX", "CY", "W", "H"}


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def name_tokens(raw: Any) -> List[str]:
    if raw is None:
        return []
    s = str(raw)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = s.replace("/", " ").replace("-", " ").replace("_", " ")
    return re.findall(r"[a-z0-9]+", s.lower())


def read_json(path: str) -> Any:
    if path == "-" or not path:
        raw = sys.stdin.read()
        if not raw.strip():
            raise ValueError("stdin is empty")
        return json.loads(raw)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json_atomic(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp.", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            # Human-readable state/output files should preserve Unicode characters.
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def load_state(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict):
        raise ValueError("state must be a JSON object")
    if state.get("version") != STATE_VERSION:
        raise ValueError(f"unsupported state version: {state.get('version')}")
    return state


def save_state(path: str, state: Dict[str, Any]) -> None:
    write_json_atomic(path, state)


def select_root_node(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("document"), dict):
        return payload["document"]
    if isinstance(payload, dict):
        return payload
    raise ValueError("input JSON root must be an object")


def pick_first_visible_paint(paints: Any, paint_type: str) -> Optional[Dict[str, Any]]:
    if not isinstance(paints, list):
        return None
    want = paint_type.upper()
    for p in paints:
        if not isinstance(p, dict):
            continue
        if p.get("visible", True) is False:
            continue
        if str(p.get("type", "")).upper() != want:
            continue
        return p
    return None


def color_spec(color_obj: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(color_obj, dict):
        return None
    token = color_obj.get("colorVariableName")
    hex_rgba = color_obj.get("hexRGBA")
    out: Dict[str, Any] = {}
    if isinstance(token, str) and token.strip():
        out["token"] = token.strip()
    if isinstance(hex_rgba, str) and hex_rgba.strip():
        out["fallbackHex"] = hex_rgba.strip()
    return out or None


def extract_solid_fill_color(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    paint = pick_first_visible_paint(node.get("fills"), "SOLID")
    if not paint:
        return None
    c = color_spec(paint.get("color"))
    if not c:
        return None
    out: Dict[str, Any] = {"color": c}
    if is_number(paint.get("opacity")) and float(paint["opacity"]) != 1.0:
        out["opacity"] = float(paint["opacity"])
    return out


def extract_solid_stroke(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    paint = pick_first_visible_paint(node.get("strokes"), "SOLID")
    if not paint:
        return None
    c = color_spec(paint.get("color"))
    if not c:
        return None
    out: Dict[str, Any] = {"color": c}
    weight = node.get("strokeWeight", node.get("strokeWidth"))
    if is_number(weight) and float(weight) != 0.0:
        out["width"] = float(weight)
    align = node.get("strokeAlign")
    if isinstance(align, str) and align:
        out["align"] = align
    if is_number(paint.get("opacity")) and float(paint["opacity"]) != 1.0:
        out["opacity"] = float(paint["opacity"])
    return out


def extract_image(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    paint = pick_first_visible_paint(node.get("fills"), "IMAGE")
    image_hash = None
    if isinstance(paint, dict):
        image_hash = paint.get("imageHash") or paint.get("imageRef")
    image_hash = image_hash or node.get("imageHash") or node.get("imageRef")
    if not paint and not image_hash:
        return None
    out: Dict[str, Any] = {}
    if isinstance(image_hash, str) and image_hash:
        out["imageHash"] = image_hash
    if isinstance(paint, dict):
        scale = paint.get("scaleMode")
        if isinstance(scale, str) and scale:
            out["scaleMode"] = scale
        if is_number(paint.get("opacity")) and float(paint["opacity"]) != 1.0:
            out["opacity"] = float(paint["opacity"])
    return out or None


def extract_font(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    token = node.get("textVariableName") or node.get("textStyleVariableName")
    fallback: Dict[str, Any] = {}

    font_name = node.get("fontName")
    font_size = node.get("fontSize")
    if isinstance(font_name, str) and font_name.strip():
        fallback["name"] = font_name.strip()
    if is_number(font_size):
        fallback["size"] = float(font_size)

    style = node.get("style")
    if isinstance(style, dict):
        if "name" not in fallback:
            v = style.get("fontPostScriptName") or style.get("fontFamily")
            if isinstance(v, str) and v.strip():
                fallback["name"] = v.strip()
        if "size" not in fallback and is_number(style.get("fontSize")):
            fallback["size"] = float(style["fontSize"])
        if is_number(style.get("fontWeight")):
            fallback["weight"] = float(style["fontWeight"])

    out: Dict[str, Any] = {}
    if isinstance(token, str) and token.strip():
        out["token"] = token.strip()
    if fallback:
        out["fallback"] = fallback
    return out or None


def extract_shadows(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    effects = node.get("effects")
    if not isinstance(effects, list):
        return []
    out: List[Dict[str, Any]] = []
    for eff in effects:
        if not isinstance(eff, dict):
            continue
        if eff.get("visible", True) is False:
            continue
        t = str(eff.get("type", "")).upper()
        if t not in ("DROP_SHADOW", "INNER_SHADOW"):
            continue
        shadow: Dict[str, Any] = {"type": t}
        c = color_spec(eff.get("color"))
        if c:
            shadow["color"] = c
        offset = eff.get("offset")
        if isinstance(offset, dict) and is_number(offset.get("x")) and is_number(offset.get("y")):
            shadow["offset"] = {"x": float(offset["x"]), "y": float(offset["y"])}
        if is_number(eff.get("radius")):
            shadow["radius"] = float(eff["radius"])
        if is_number(eff.get("spread")):
            shadow["spread"] = float(eff["spread"])
        # Some exports may include opacity at top-level or embedded in color alpha; keep best-effort.
        if is_number(eff.get("opacity")) and float(eff["opacity"]) != 1.0:
            shadow["opacity"] = float(eff["opacity"])
        out.append(shadow)
    return out


def node_facts(node: Dict[str, Any], *, max_text_len: int) -> Dict[str, Any]:
    """
    Extract a compact "facts" object for the LLM (not the final spec).
    """
    figma_type = str(node.get("type", "")).upper()

    facts: Dict[str, Any] = {
        "nameTokens": name_tokens(node.get("name")),
        "visible": bool(node.get("visible", True)),
        "locked": bool(node.get("locked", False)),
    }

    # Geometry (used only for derivation; final spec should not include raw frame).
    frame: Dict[str, Any] = {}
    for k in ("x", "y", "width", "height", "rotation"):
        v = node.get(k)
        if is_number(v):
            frame[k] = float(v)
    if frame:
        facts["frame"] = frame

    layout: Dict[str, Any] = {}
    for k in (
        "layoutMode",
        "layoutPositioning",
        "layoutSizingHorizontal",
        "layoutSizingVertical",
        "constraints",
        "layoutGrow",
        "layoutAlign",
        "itemSpacing",
        "paddingLeft",
        "paddingRight",
        "paddingTop",
        "paddingBottom",
        "primaryAxisAlignItems",
        "counterAxisAlignItems",
        "layoutWrap",
    ):
        v = node.get(k)
        if v is not None:
            layout[k] = v
    if layout:
        facts["layout"] = layout

    style: Dict[str, Any] = {}
    bg = extract_solid_fill_color(node)
    if bg:
        style["backgroundColor"] = bg
    stroke = extract_solid_stroke(node)
    if stroke:
        style["stroke"] = stroke
    cr = node.get("cornerRadius")
    if is_number(cr) and float(cr) != 0.0:
        style["cornerRadius"] = float(cr)
    opacity = node.get("opacity")
    if is_number(opacity) and float(opacity) != 1.0:
        style["opacity"] = float(opacity)
    if node.get("clipsContent") is True:
        style["clipsContent"] = True
    shadows = extract_shadows(node)
    if shadows:
        style["shadows"] = shadows
    if style:
        facts["style"] = style

    if figma_type == "TEXT":
        text: Dict[str, Any] = {}
        chars = node.get("characters") or node.get("text")
        if isinstance(chars, str) and chars:
            if max_text_len >= 0 and len(chars) > max_text_len:
                text["characters"] = chars[:max_text_len] + "..."
                text["charactersTruncated"] = True
            else:
                text["characters"] = chars
        f = extract_font(node)
        if f:
            text["font"] = f
        # In Figma, TEXT fills encode glyph color.
        fill = extract_solid_fill_color(node)
        if fill:
            text["textColor"] = fill.get("color")
        if text:
            facts["text"] = text

    img = extract_image(node)
    if img:
        facts["image"] = img

    return facts


@dataclass(frozen=True)
class NodeRecord:
    id: str
    name: str
    type: str
    parent_id: Optional[str]
    depth: int
    child_ids: List[str]
    facts: Dict[str, Any]


def should_include_node(node: Dict[str, Any], *, include_invisible: bool) -> bool:
    if include_invisible:
        return True
    return bool(node.get("visible", True))


def index_tree(
    root: Dict[str, Any],
    *,
    include_invisible: bool,
    max_text_len: int,
) -> Tuple[str, Dict[str, Any]]:
    """
    Build a flat id->record map from a Figma node tree.

    Records are stored as plain JSON objects (not dataclasses) so they can be serialized.
    """
    if not isinstance(root.get("id"), str) or not root["id"]:
        raise ValueError("root node is missing a string 'id'")

    nodes: Dict[str, Any] = {}

    def visit(node: Dict[str, Any], parent_id: Optional[str], depth: int) -> None:
        if not should_include_node(node, include_invisible=include_invisible):
            return

        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            return
        if node_id in nodes:
            # Figma ids should be unique; if we see duplicates, keep the first.
            return

        children_raw = node.get("children")
        child_ids: List[str] = []
        if isinstance(children_raw, list):
            for c in children_raw:
                if not isinstance(c, dict):
                    continue
                if not should_include_node(c, include_invisible=include_invisible):
                    continue
                cid = c.get("id")
                if isinstance(cid, str) and cid:
                    child_ids.append(cid)

        rec = {
            "id": node_id,
            "name": str(node.get("name", "")),
            "type": str(node.get("type", "")),
            "parentId": parent_id,
            "depth": depth,
            "childIds": child_ids,
            "facts": node_facts(node, max_text_len=max_text_len),
        }
        nodes[node_id] = rec

        if isinstance(children_raw, list):
            for c in children_raw:
                if isinstance(c, dict):
                    visit(c, node_id, depth + 1)

    visit(root, None, 0)
    return root["id"], nodes


def bfs_order(nodes: Dict[str, Any], root_id: str) -> List[str]:
    order: List[str] = []
    q: List[str] = [root_id]
    seen: set[str] = set()
    while q:
        nid = q.pop(0)
        if nid in seen:
            continue
        seen.add(nid)
        if nid not in nodes:
            continue
        order.append(nid)
        q.extend(list(nodes[nid].get("childIds", [])))
    return order


def node_skeleton(nodes: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    n = nodes.get(node_id)
    if not isinstance(n, dict):
        return None
    return {
        "id": n.get("id"),
        "name": n.get("name"),
        "type": n.get("type"),
        "depth": n.get("depth"),
        "childCount": len(n.get("childIds") or []),
    }


def skeleton_tree(nodes: Dict[str, Any], node_id: str, depth: int) -> Optional[Dict[str, Any]]:
    sk = node_skeleton(nodes, node_id)
    if not sk:
        return None
    if depth <= 0:
        return sk
    child_ids = nodes[node_id].get("childIds") or []
    sk["children"] = []
    for cid in child_ids:
        child_sk = skeleton_tree(nodes, cid, depth - 1)
        if child_sk:
            sk["children"].append(child_sk)
    return sk


def base_component_from_decision(decision: Any) -> Optional[str]:
    if not isinstance(decision, dict):
        return None
    comp = decision.get("component")
    if not isinstance(comp, dict):
        return None
    base = comp.get("base")
    if isinstance(base, str) and base.strip():
        return base.strip()
    return None


def requirements_for_child(parent_decision: Any) -> Dict[str, Any]:
    base = base_component_from_decision(parent_decision)
    if not base:
        return {}

    # UIKit rules
    if base == "UICollectionView":
        return {"mustUseComponentBase": "UICollectionViewCell"}
    if base == "UITableView":
        return {"mustUseComponentBase": "UITableViewCell"}
    if base == "UIButton":
        return {"allowedComponentBases": ["UILabel", "UIImageView"], "role": "button-contained"}

    # SwiftUI rules (hints only; SwiftUI composition is more flexible)
    if base == "List":
        return {"hint": "List children should be row views (usually custom Views)."}
    if base == "Button":
        return {"allowedComponentBases": ["Text", "Image", "Label", "View"], "role": "button-label"}
    return {}


def parse_pins(pins: Any) -> Tuple[Optional[Dict[str, float]], List[str]]:
    """
    Parse "pins=L=0:R=-6:CY=0" into {"L": 0.0, "R": -6.0, "CY": 0.0}.
    Returns (parsed, errors).
    """
    if not isinstance(pins, str) or not pins.strip():
        return None, ["pins_not_a_string"]
    s = pins.strip()
    if not s.startswith("pins="):
        return None, ["pins_missing_prefix"]
    body = s[len("pins=") :].strip()
    if not body:
        return None, ["pins_empty"]

    out: Dict[str, float] = {}
    errors: List[str] = []

    for part in body.split(":"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            errors.append(f"pins_bad_part:{part}")
            continue
        key, raw_val = part.split("=", 1)
        key = key.strip().upper()
        raw_val = raw_val.strip()
        if key not in PINS_ALLOWED_KEYS:
            errors.append(f"pins_unknown_key:{key}")
            continue
        try:
            val = float(raw_val)
        except ValueError:
            errors.append(f"pins_bad_value:{key}={raw_val}")
            continue
        out[key] = val

    if errors:
        return None, errors
    if not out:
        return None, ["pins_no_pairs"]
    return out, []


def component_hint_from_node_record(node_rec: Dict[str, Any], *, ui_system: str) -> Optional[Dict[str, Any]]:
    """
    Provide a deterministic, name/type/layout-based component hint.

    This is a hint for the LLM (not enforced).
    """
    if not isinstance(node_rec, dict):
        return None

    figma_type = str(node_rec.get("type", "")).upper()
    facts = node_rec.get("facts") or {}
    if not isinstance(facts, dict):
        facts = {}

    tokens = facts.get("nameTokens") or []
    if not isinstance(tokens, list):
        tokens = []

    layout = facts.get("layout") or {}
    if not isinstance(layout, dict):
        layout = {}
    layout_mode = str(layout.get("layoutMode", "")).upper()

    # Token groups
    has = set(str(t) for t in tokens if isinstance(t, str))
    is_table_hint = bool(has.intersection({"table", "tableview", "list", "feed"}))
    is_collection_hint = bool(has.intersection({"collection", "collectionview", "grid", "carousel", "gallery"}))
    is_scroll_hint = bool(has.intersection({"scroll", "scrollview", "scroller", "pager", "pageview"}))
    is_button_hint = bool(has.intersection({"button", "btn", "cta"}))
    is_icon_hint = bool(has.intersection({"icon", "image", "img", "avatar", "photo", "logo", "thumbnail", "thumb"}))
    is_label_hint = bool(has.intersection({"label", "title", "subtitle", "caption", "headline", "body", "description", "text"}))

    reasons: List[str] = []

    # Strongest: explicit Figma node types.
    if figma_type == "TEXT":
        reasons.append("figma_type=TEXT")
        base = "UILabel" if ui_system == "UIKit" else "Text"
        return {"base": base, "reasons": reasons}
    if figma_type == "IMAGE":
        reasons.append("figma_type=IMAGE")
        base = "UIImageView" if ui_system == "UIKit" else "Image"
        return {"base": base, "reasons": reasons}

    # Auto-layout containers.
    if layout_mode in ("HORIZONTAL", "VERTICAL"):
        reasons.append(f"layoutMode={layout_mode}")
        if ui_system == "UIKit":
            return {"base": "UIStackView", "reasons": reasons}
        return {"base": "HStack" if layout_mode == "HORIZONTAL" else "VStack", "reasons": reasons}

    # Name-based semantics.
    if is_table_hint or is_collection_hint or is_scroll_hint:
        reasons.append("name_suggests_list_or_scroll")
        if ui_system == "UIKit":
            if is_collection_hint:
                return {"base": "UICollectionView", "reasons": reasons}
            if is_table_hint:
                return {"base": "UITableView", "reasons": reasons}
            return {"base": "UICollectionView", "reasons": reasons}
        if is_table_hint and not is_collection_hint:
            return {"base": "List", "reasons": reasons}
        return {"base": "ScrollView", "reasons": reasons}

    if is_button_hint:
        reasons.append("name_suggests_button")
        return {"base": "UIButton" if ui_system == "UIKit" else "Button", "reasons": reasons}

    if is_icon_hint:
        reasons.append("name_suggests_image")
        return {"base": "UIImageView" if ui_system == "UIKit" else "Image", "reasons": reasons}

    if is_label_hint:
        reasons.append("name_suggests_text")
        return {"base": "UILabel" if ui_system == "UIKit" else "Text", "reasons": reasons}

    # Default.
    return {"base": "UIView" if ui_system == "UIKit" else "View", "reasons": ["default"]}


def cell_sizing_hint_from_node_record(node_rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Heuristic hint for UIKit cell sizing.
    - If either axis is HUG -> selfSizing
    - Else -> fixed with fixedSize from frame
    """
    if not isinstance(node_rec, dict):
        return None
    facts = node_rec.get("facts") or {}
    if not isinstance(facts, dict):
        return None
    layout = facts.get("layout") or {}
    frame = facts.get("frame") or {}
    if not isinstance(layout, dict) or not isinstance(frame, dict):
        return None

    h = str(layout.get("layoutSizingHorizontal", "")).upper()
    v = str(layout.get("layoutSizingVertical", "")).upper()
    if h == "HUG" or v == "HUG":
        return {"cellSizing": "selfSizing", "reason": "layoutSizingHorizontal/Vertical has HUG"}

    if is_number(frame.get("width")) and is_number(frame.get("height")):
        return {
            "cellSizing": "fixed",
            "fixedSize": {"width": float(frame["width"]), "height": float(frame["height"])},
            "reason": "no HUG sizing; using frame width/height",
        }
    return {"cellSizing": "fixed", "reason": "no HUG sizing; missing frame width/height"}


def content_mode_hint_from_facts(facts: Dict[str, Any], *, ui_system: str) -> Optional[str]:
    """
    Suggest a UIKit contentMode / SwiftUI resizable behavior from Figma image scaleMode.
    """
    if not isinstance(facts, dict):
        return None
    image = facts.get("image")
    if not isinstance(image, dict):
        return None
    scale = str(image.get("scaleMode", "")).upper()
    if not scale:
        return None

    if ui_system == "UIKit":
        if scale == "FIT":
            return "scaleAspectFit"
        if scale == "FILL":
            return "scaleAspectFill"
        if scale == "STRETCH":
            return "scaleToFill"
        if scale == "TILE":
            return "center"  # best-effort; tiled images usually require custom layer handling
        return None

    # SwiftUI: high-level hint (not a direct API value).
    if scale == "FIT":
        return "aspectRatio(.fit)"
    if scale == "FILL":
        return "aspectRatio(.fill)"
    return None


def compute_constraint_hints(
    nodes: Dict[str, Any],
    node_id: str,
    parent_id: Optional[str],
) -> Optional[str]:
    """
    Best-effort: derive a candidate constraints list from Figma constraints + geometry.

    This is a hint for the LLM, not the final truth.
    """
    if not parent_id:
        return None
    n = nodes.get(node_id)
    p = nodes.get(parent_id)
    if not isinstance(n, dict) or not isinstance(p, dict):
        return None

    facts = n.get("facts", {})
    pfacts = p.get("facts", {})
    if not isinstance(facts, dict) or not isinstance(pfacts, dict):
        return None

    layout = facts.get("layout", {})
    frame = facts.get("frame", {})
    pframe = pfacts.get("frame", {})
    if not isinstance(layout, dict) or not isinstance(frame, dict) or not isinstance(pframe, dict):
        return None

    # If the parent is an auto-layout container, arranged-subview positioning is driven by the parent.
    # Only compute pins hints when the child opts into ABSOLUTE positioning.
    parent_layout = pfacts.get("layout", {})
    if isinstance(parent_layout, dict):
        parent_layout_mode = str(parent_layout.get("layoutMode", "")).upper()
        child_positioning = str(layout.get("layoutPositioning", "")).upper()
        if parent_layout_mode in ("HORIZONTAL", "VERTICAL") and child_positioning != "ABSOLUTE":
            return None

    constraints = layout.get("constraints")
    if not isinstance(constraints, dict):
        return None

    x = frame.get("x")
    y = frame.get("y")
    w = frame.get("width")
    h = frame.get("height")
    pw = pframe.get("width")
    ph = pframe.get("height")

    if not (is_number(x) and is_number(y) and is_number(w) and is_number(h) and is_number(pw) and is_number(ph)):
        return None

    horiz = str(constraints.get("horizontal", "")).upper()
    vert = str(constraints.get("vertical", "")).upper()

    pins: List[str] = []

    def add_pin(key: str, value: float) -> None:
        # Keep integers readable; otherwise keep a stable float representation.
        if float(value).is_integer():
            pins.append(f"{key}={int(value)}")
        else:
            pins.append(f"{key}={float(value)}")

    # Horizontal anchors
    if horiz == "MIN":
        add_pin("L", float(x))
    elif horiz == "MAX":
        inset = float(pw) - (float(x) + float(w))
        add_pin("R", -inset)
    elif horiz == "CENTER":
        cx = (float(x) + float(w) / 2.0) - float(pw) / 2.0
        add_pin("CX", cx)
    elif horiz == "STRETCH":
        add_pin("L", float(x))
        inset = float(pw) - (float(x) + float(w))
        add_pin("R", -inset)
    elif horiz == "SCALE":
        # Too many interpretations; emit only a conservative anchor.
        add_pin("L", float(x))

    # Vertical anchors
    if vert == "MIN":
        add_pin("T", float(y))
    elif vert == "MAX":
        inset = float(ph) - (float(y) + float(h))
        add_pin("B", -inset)
    elif vert == "CENTER":
        cy = (float(y) + float(h) / 2.0) - float(ph) / 2.0
        add_pin("CY", cy)
    elif vert == "STRETCH":
        add_pin("T", float(y))
        inset = float(ph) - (float(y) + float(h))
        add_pin("B", -inset)
    elif vert == "SCALE":
        add_pin("T", float(y))

    # Size hints based on layoutSizing* (if present) else fixed.
    sizing_h = str(layout.get("layoutSizingHorizontal", "")).upper()
    sizing_v = str(layout.get("layoutSizingVertical", "")).upper()
    if sizing_h == "FIXED" or sizing_h == "":
        add_pin("W", float(w))
    if sizing_v == "FIXED" or sizing_v == "":
        add_pin("H", float(h))

    if not pins:
        return None
    # Deduplicate keys while keeping first occurrence stable.
    seen_keys: set[str] = set()
    uniq_pins: List[str] = []
    for part in pins:
        k = part.split("=", 1)[0]
        if k in seen_keys:
            continue
        seen_keys.add(k)
        uniq_pins.append(part)
    return "pins=" + ":".join(uniq_pins)


def cmd_init(args: argparse.Namespace) -> int:
    try:
        payload = read_json(args.input)
        root = select_root_node(payload)
        root_id, nodes = index_tree(
            root,
            include_invisible=args.include_invisible,
            max_text_len=args.max_text_len,
        )
        bfs = bfs_order(nodes, root_id)
    except Exception as exc:
        eprint(f"error: init failed: {exc}")
        return 2

    state: Dict[str, Any] = {
        "version": STATE_VERSION,
        "uiSystem": args.ui_system,
        "rootId": root_id,
        "nodes": nodes,
        "bfs": bfs,
        "decisions": {},
    }
    save_state(args.state, state)
    json.dump(
        {"ok": True, "state": args.state, "rootId": root_id, "nodeCount": len(nodes), "bfsCount": len(bfs)},
        sys.stdout,
        ensure_ascii=False,
        indent=2,
    )
    sys.stdout.write("\n")
    return 0


def cmd_skeleton(args: argparse.Namespace) -> int:
    try:
        state = load_state(args.state)
        nodes = state["nodes"]
        node_id = args.node_id or state["rootId"]
        sk = skeleton_tree(nodes, node_id, args.depth)
        if not sk:
            raise ValueError(f"unknown node id: {node_id}")
    except Exception as exc:
        eprint(f"error: skeleton failed: {exc}")
        return 2

    json.dump({"node": sk}, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


def cmd_children(args: argparse.Namespace) -> int:
    try:
        state = load_state(args.state)
        nodes = state["nodes"]
        nid = args.node_id
        if nid not in nodes:
            raise ValueError(f"unknown node id: {nid}")
        child_ids = nodes[nid].get("childIds") or []
        out = []
        for cid in child_ids:
            sk = node_skeleton(nodes, cid)
            if sk:
                out.append(sk)
    except Exception as exc:
        eprint(f"error: children failed: {exc}")
        return 2

    json.dump({"nodeId": nid, "children": out}, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


def cmd_facts(args: argparse.Namespace) -> int:
    try:
        state = load_state(args.state)
        nodes = state["nodes"]
        nid = args.node_id
        if nid not in nodes:
            raise ValueError(f"unknown node id: {nid}")
        facts = nodes[nid].get("facts") or {}
    except Exception as exc:
        eprint(f"error: facts failed: {exc}")
        return 2

    json.dump({"nodeId": nid, "facts": facts}, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


def _next_node_id(state: Dict[str, Any]) -> Optional[str]:
    bfs = state.get("bfs") or []
    decisions = state.get("decisions") or {}
    if not isinstance(bfs, list) or not isinstance(decisions, dict):
        return None
    for nid in bfs:
        if isinstance(nid, str) and nid and nid not in decisions:
            return nid
    return None


def cmd_next(args: argparse.Namespace) -> int:
    try:
        state = load_state(args.state)
        nodes = state["nodes"]
        decisions = state.get("decisions") or {}
        nid = _next_node_id(state)
        if not nid:
            json.dump({"done": True}, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
            sys.stdout.write("\n")
            return 0
        n = nodes.get(nid)
        if not isinstance(n, dict):
            raise ValueError(f"corrupt node record: {nid}")

        parent_id = n.get("parentId")
        parent_sk = node_skeleton(nodes, parent_id) if isinstance(parent_id, str) else None
        parent_decision = decisions.get(parent_id) if isinstance(parent_id, str) else None

        req = requirements_for_child(parent_decision)

        child_ids = n.get("childIds") or []
        children = []
        for cid in child_ids:
            sk = node_skeleton(nodes, cid)
            if sk:
                children.append(sk)

        ui_system = str(state.get("uiSystem") or "")

        hints: Dict[str, Any] = {}
        pins = compute_constraint_hints(nodes, nid, parent_id if isinstance(parent_id, str) else None)
        if pins:
            hints["pinsCandidate"] = pins

        comp_hint = component_hint_from_node_record(n, ui_system=ui_system)
        if comp_hint:
            hints["componentHint"] = comp_hint

        # UIKit cell sizing hint when required by parent.
        if ui_system == "UIKit" and isinstance(req, dict) and req.get("mustUseComponentBase") in (
            "UITableViewCell",
            "UICollectionViewCell",
        ):
            cs = cell_sizing_hint_from_node_record(n)
            if cs:
                hints["cellSizingHint"] = cs

        cm = content_mode_hint_from_facts(n.get("facts") or {}, ui_system=ui_system)
        if cm:
            hints["contentModeHint"] = cm

        out = {
            "uiSystem": ui_system,
            "node": node_skeleton(nodes, nid),
            "parent": parent_sk,
            "parentDecision": parent_decision,
            "requirements": req,
            "facts": n.get("facts") or {},
            "children": children,
            "hints": hints,
        }
    except Exception as exc:
        eprint(f"error: next failed: {exc}")
        return 2

    json.dump(out, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


def normalize_patch_payload(payload: Any) -> List[Dict[str, Any]]:
    """
    Supported patch shapes:
      - { "id": "...", ...decisionFields }
      - [ { "id": "...", ... }, ... ]
      - { "decisions": { "id": {...}, "id2": {...} } }
    Returns a list of {id, decision}.
    """
    if isinstance(payload, list):
        out: List[Dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            nid = item.get("id")
            if not isinstance(nid, str) or not nid:
                continue
            decision = dict(item)
            decision.pop("id", None)
            out.append({"id": nid, "decision": decision})
        return out

    if isinstance(payload, dict) and "decisions" in payload:
        decisions = payload.get("decisions")
        if isinstance(decisions, dict):
            out = []
            for nid, dec in decisions.items():
                if not isinstance(nid, str) or not nid or not isinstance(dec, dict):
                    continue
                out.append({"id": nid, "decision": dec})
            return out

    if isinstance(payload, dict):
        nid = payload.get("id")
        if isinstance(nid, str) and nid:
            decision = dict(payload)
            decision.pop("id", None)
            return [{"id": nid, "decision": decision}]

    raise ValueError("unsupported patch JSON shape")


def cmd_apply(args: argparse.Namespace) -> int:
    try:
        state = load_state(args.state)
        nodes = state["nodes"]
        decisions = state.get("decisions")
        if not isinstance(decisions, dict):
            decisions = {}
            state["decisions"] = decisions

        patch_payload = read_json(args.patch)
        patches = normalize_patch_payload(patch_payload)

        applied: List[str] = []
        for p in patches:
            nid = p["id"]
            if nid not in nodes:
                eprint(f"warning: patch references unknown node id {nid}; skipped")
                continue
            decision = p["decision"]
            if not isinstance(decision, dict):
                eprint(f"warning: decision for {nid} is not an object; skipped")
                continue
            decisions[nid] = decision
            applied.append(nid)

        save_state(args.state, state)
    except Exception as exc:
        eprint(f"error: apply failed: {exc}")
        return 2

    json.dump({"ok": True, "applied": applied, "appliedCount": len(applied)}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


def build_export_tree(state: Dict[str, Any], node_id: str) -> Dict[str, Any]:
    nodes = state["nodes"]
    decisions = state.get("decisions") or {}
    n = nodes.get(node_id) or {}

    source = {"id": n.get("id"), "name": n.get("name"), "type": n.get("type")}
    decision = decisions.get(node_id)

    out: Dict[str, Any] = {"source": source}
    if isinstance(decision, dict) and decision:
        # Merge decision keys at top-level (component/layout/properties/...)
        for k, v in decision.items():
            out[k] = v
    else:
        out["component"] = {"base": "__UNDECIDED__"}
        out["warnings"] = ["missing_decision"]

    child_ids = n.get("childIds") or []
    children_out: List[Dict[str, Any]] = []
    for cid in child_ids:
        if cid in nodes:
            children_out.append(build_export_tree(state, cid))
    out["children"] = children_out
    return out


def cmd_export(args: argparse.Namespace) -> int:
    try:
        state = load_state(args.state)
        root_id = state["rootId"]
        tree = build_export_tree(state, root_id)
        out = {"uiSystem": state.get("uiSystem"), "root": tree}

        if not args.no_absorb:
            absorb_children_in_export_tree(out.get("root"), ui_system=str(state.get("uiSystem") or ""))
    except Exception as exc:
        eprint(f"error: export failed: {exc}")
        return 2

    if args.output:
        write_json_atomic(args.output, out if args.pretty else out)
    else:
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
        sys.stdout.write("\n")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    try:
        state = load_state(args.state)
        nodes = state["nodes"]
        decisions = state.get("decisions") or {}
        bfs = state.get("bfs") or []
        total = len(nodes) if isinstance(nodes, dict) else 0
        decided = len(decisions) if isinstance(decisions, dict) else 0
        remaining = max(0, total - decided)
        next_id = _next_node_id(state)
        out = {
            "uiSystem": state.get("uiSystem"),
            "rootId": state.get("rootId"),
            "nodeCount": total,
            "decidedCount": decided,
            "remainingCount": remaining,
            "bfsCount": len(bfs) if isinstance(bfs, list) else 0,
            "nextNodeId": next_id,
        }
    except Exception as exc:
        eprint(f"error: status failed: {exc}")
        return 2

    json.dump(out, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


def get_component_base_from_spec(spec_node: Any) -> Optional[str]:
    if not isinstance(spec_node, dict):
        return None
    comp = spec_node.get("component")
    if not isinstance(comp, dict):
        return None
    base = comp.get("base")
    if isinstance(base, str) and base.strip():
        return base.strip()
    return None


def get_properties_dict(spec_node: Dict[str, Any]) -> Dict[str, Any]:
    props = spec_node.get("properties")
    if isinstance(props, dict):
        return props
    props = {}
    spec_node["properties"] = props
    return props


def absorb_children_in_export_tree(spec_node: Any, *, ui_system: str) -> None:
    """
    Perform deterministic "child absorb" on the exported spec tree.

    UIKit:
      - UIButton absorbs direct UILabel/UIImageView children into button properties
      - UIImageView absorbs direct UIImageView child image into itself
    SwiftUI:
      - Button absorbs direct Text/Image children into button properties (title/image)
      - Image absorbs direct Image child image into itself

    Absorbed children are removed from `children` but their source ids are recorded
    via `titleFrom` / `imageFrom` fields on the parent for traceability.
    """
    if not isinstance(spec_node, dict):
        return

    children = spec_node.get("children")
    if isinstance(children, list):
        for c in children:
            absorb_children_in_export_tree(c, ui_system=ui_system)

    base = get_component_base_from_spec(spec_node) or ""
    if not base:
        return

    children = spec_node.get("children")
    if not isinstance(children, list) or not children:
        return

    def child_source_id(child: Dict[str, Any]) -> Optional[str]:
        src = child.get("source")
        if isinstance(src, dict):
            cid = src.get("id")
            if isinstance(cid, str) and cid:
                return cid
        return None

    def child_properties(child: Dict[str, Any]) -> Dict[str, Any]:
        p = child.get("properties")
        return p if isinstance(p, dict) else {}

    remove_idx: set[int] = set()
    props = get_properties_dict(spec_node)

    if ui_system == "UIKit":
        if base == "UIButton":
            label_i = None
            image_i = None
            for i, c in enumerate(children):
                cbase = get_component_base_from_spec(c) or ""
                if label_i is None and cbase == "UILabel":
                    label_i = i
                if image_i is None and cbase == "UIImageView":
                    image_i = i

            if label_i is not None:
                c = children[label_i]
                cp = child_properties(c)
                if "title" not in props and isinstance(cp.get("text"), str):
                    props["title"] = cp["text"]
                    cid = child_source_id(c)
                    if cid:
                        props["titleFrom"] = cid
                if "titleColor" not in props and isinstance(cp.get("textColor"), dict):
                    props["titleColor"] = cp["textColor"]
                if "titleFont" not in props and isinstance(cp.get("font"), dict):
                    props["titleFont"] = cp["font"]
                remove_idx.add(label_i)

            if image_i is not None:
                c = children[image_i]
                cp = child_properties(c)
                img = cp.get("image")
                if "image" not in props and isinstance(img, dict):
                    props["image"] = img
                    cid = child_source_id(c)
                    if cid:
                        props["imageFrom"] = cid
                if "imageContentMode" not in props and isinstance(cp.get("contentMode"), str):
                    props["imageContentMode"] = cp["contentMode"]
                remove_idx.add(image_i)

        if base == "UIImageView":
            # If already has image, keep.
            if isinstance(props.get("image"), dict):
                return
            for i, c in enumerate(children):
                if (get_component_base_from_spec(c) or "") != "UIImageView":
                    continue
                cp = child_properties(c)
                img = cp.get("image")
                if isinstance(img, dict):
                    props["image"] = img
                    cid = child_source_id(c)
                    if cid:
                        props["imageFrom"] = cid
                    remove_idx.add(i)
                    break

    if ui_system == "SwiftUI":
        if base == "Button":
            text_i = None
            image_i = None
            for i, c in enumerate(children):
                cbase = get_component_base_from_spec(c) or ""
                if text_i is None and cbase == "Text":
                    text_i = i
                if image_i is None and cbase == "Image":
                    image_i = i

            if text_i is not None:
                c = children[text_i]
                cp = child_properties(c)
                if "title" not in props and isinstance(cp.get("text"), str):
                    props["title"] = cp["text"]
                    cid = child_source_id(c)
                    if cid:
                        props["titleFrom"] = cid
                remove_idx.add(text_i)

            if image_i is not None:
                c = children[image_i]
                cp = child_properties(c)
                img = cp.get("image")
                if "image" not in props and isinstance(img, dict):
                    props["image"] = img
                    cid = child_source_id(c)
                    if cid:
                        props["imageFrom"] = cid
                remove_idx.add(image_i)

        if base == "Image":
            if isinstance(props.get("image"), dict):
                return
            for i, c in enumerate(children):
                if (get_component_base_from_spec(c) or "") != "Image":
                    continue
                cp = child_properties(c)
                img = cp.get("image")
                if isinstance(img, dict):
                    props["image"] = img
                    cid = child_source_id(c)
                    if cid:
                        props["imageFrom"] = cid
                    remove_idx.add(i)
                    break

    if remove_idx:
        spec_node["children"] = [c for i, c in enumerate(children) if i not in remove_idx]


def cmd_validate(args: argparse.Namespace) -> int:
    """
    Validate decisions in the state file against the deterministic spec contract.
    """
    try:
        state = load_state(args.state)
        nodes = state.get("nodes") or {}
        decisions = state.get("decisions") or {}
        ui_system = str(state.get("uiSystem") or "")
        if not isinstance(nodes, dict) or not isinstance(decisions, dict):
            raise ValueError("corrupt state (nodes/decisions)")
    except Exception as exc:
        eprint(f"error: validate failed to load state: {exc}")
        return 2

    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    def err(node_id: str, msg: str, *, detail: Any = None) -> None:
        item: Dict[str, Any] = {"nodeId": node_id, "error": msg}
        if detail is not None:
            item["detail"] = detail
        errors.append(item)

    def warn(node_id: str, msg: str, *, detail: Any = None) -> None:
        item: Dict[str, Any] = {"nodeId": node_id, "warning": msg}
        if detail is not None:
            item["detail"] = detail
        warnings.append(item)

    # Validate per-node decision payload shape.
    for node_id in nodes.keys():
        dec = decisions.get(node_id)
        if dec is None:
            warn(str(node_id), "missing_decision")
            continue
        if not isinstance(dec, dict):
            err(str(node_id), "decision_not_object")
            continue

        comp = dec.get("component")
        if not isinstance(comp, dict):
            err(str(node_id), "missing_component_object")
            continue
        base = comp.get("base")
        if not isinstance(base, str) or not base.strip():
            err(str(node_id), "missing_component_base")

        layout = dec.get("layout")
        if layout is not None and not isinstance(layout, dict):
            err(str(node_id), "layout_not_object")
            layout = None

        if isinstance(layout, dict):
            kind = layout.get("kind")
            if kind is not None and not isinstance(kind, str):
                err(str(node_id), "layout_kind_not_string")
            if isinstance(kind, str) and kind not in ("root", "stack", "stackItem", "pins", "list", "scroll"):
                warn(str(node_id), "layout_kind_unknown", detail=kind)

            pins = layout.get("pins")
            if pins is not None:
                parsed, perr = parse_pins(pins)
                if perr:
                    err(str(node_id), "invalid_pins_string", detail=perr)

            if kind == "stack":
                axis = layout.get("axis")
                if axis not in ("horizontal", "vertical"):
                    err(str(node_id), "stack_axis_missing_or_invalid")

        # Cell sizing rules (UIKit)
        if ui_system == "UIKit" and isinstance(base, str):
            if base.strip() in ("UITableViewCell", "UICollectionViewCell"):
                if not isinstance(layout, dict):
                    err(str(node_id), "cell_missing_layout")
                else:
                    cs = layout.get("cellSizing")
                    if cs not in ("selfSizing", "fixed"):
                        err(str(node_id), "cellSizing_missing_or_invalid")
                    if cs == "fixed":
                        fs = layout.get("fixedSize")
                        if not isinstance(fs, dict) or not is_number(fs.get("width")) or not is_number(fs.get("height")):
                            err(str(node_id), "fixed_cell_requires_fixedSize_width_height")

    # Validate parent->child constraints (requires parent decisions).
    for node_id, rec in nodes.items():
        if not isinstance(rec, dict):
            continue
        parent_dec = decisions.get(node_id)
        parent_base = base_component_from_decision(parent_dec) or ""
        child_ids = rec.get("childIds") or []
        if not isinstance(child_ids, list) or not child_ids:
            continue

        if ui_system == "UIKit":
            if parent_base in ("UICollectionView", "UITableView"):
                must = "UICollectionViewCell" if parent_base == "UICollectionView" else "UITableViewCell"
                for cid in child_ids:
                    cdec = decisions.get(cid)
                    cbase = base_component_from_decision(cdec)
                    if cbase is None:
                        warn(str(cid), "child_missing_decision_under_list_container", detail={"parent": node_id, "mustBe": must})
                    elif cbase != must:
                        err(str(cid), "child_component_must_be_cell", detail={"parent": node_id, "mustBe": must, "actual": cbase})

            if parent_base == "UIButton":
                for cid in child_ids:
                    cdec = decisions.get(cid)
                    cbase = base_component_from_decision(cdec)
                    if cbase is None:
                        continue
                    if cbase not in ("UILabel", "UIImageView"):
                        warn(str(cid), "unexpected_child_under_UIButton", detail={"parent": node_id, "actual": cbase})

        if ui_system == "SwiftUI":
            if parent_base == "Button":
                for cid in child_ids:
                    cdec = decisions.get(cid)
                    cbase = base_component_from_decision(cdec)
                    if cbase is None:
                        continue
                    if cbase not in ("Text", "Image", "Label", "View"):
                        warn(str(cid), "unexpected_child_under_Button", detail={"parent": node_id, "actual": cbase})

            if parent_base == "List":
                for cid in child_ids:
                    cdec = decisions.get(cid)
                    cbase = base_component_from_decision(cdec)
                    if cbase is None:
                        continue
                    # Prefer row views; allow View/Text/Image for flexibility.
                    if cbase not in ("View", "Text", "Image", "HStack", "VStack", "ZStack"):
                        warn(str(cid), "unexpected_child_under_List", detail={"parent": node_id, "actual": cbase})

    ok = len(errors) == 0
    json.dump({"ok": ok, "errors": errors, "warnings": warnings}, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="figma_ios_bfs_tool.py", description="Figma -> iOS spec BFS helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Index input JSON into a BFS state file")
    p_init.add_argument("--input", required=True, help='Input JSON file path, or "-" for stdin')
    p_init.add_argument("--ui-system", required=True, choices=UI_SYSTEMS, dest="ui_system")
    p_init.add_argument("--state", default=DEFAULT_STATE_PATH, help=f"State file path (default: {DEFAULT_STATE_PATH})")
    p_init.add_argument("--include-invisible", action="store_true", help="Include nodes with visible=false")
    p_init.add_argument("--max-text-len", type=int, default=200, help="Truncate TEXT.characters in facts (default: 200; -1 = no truncation)")
    p_init.set_defaults(func=cmd_init)

    p_skel = sub.add_parser("skeleton", help="Print a depth-limited skeleton tree")
    p_skel.add_argument("--state", default=DEFAULT_STATE_PATH)
    p_skel.add_argument("--node-id", default=None)
    p_skel.add_argument("--depth", type=int, default=2)
    p_skel.add_argument("--pretty", action="store_true")
    p_skel.set_defaults(func=cmd_skeleton)

    p_children = sub.add_parser("children", help="List direct children skeletons")
    p_children.add_argument("--state", default=DEFAULT_STATE_PATH)
    p_children.add_argument("--node-id", required=True)
    p_children.add_argument("--pretty", action="store_true")
    p_children.set_defaults(func=cmd_children)

    p_facts = sub.add_parser("facts", help="Print facts for a node id")
    p_facts.add_argument("--state", default=DEFAULT_STATE_PATH)
    p_facts.add_argument("--node-id", required=True)
    p_facts.add_argument("--pretty", action="store_true")
    p_facts.set_defaults(func=cmd_facts)

    p_next = sub.add_parser("next", help="Return next undecided node context (BFS)")
    p_next.add_argument("--state", default=DEFAULT_STATE_PATH)
    p_next.add_argument("--pretty", action="store_true")
    p_next.set_defaults(func=cmd_next)

    p_apply = sub.add_parser("apply", help="Apply decision patches to the state file")
    p_apply.add_argument("--state", default=DEFAULT_STATE_PATH)
    p_apply.add_argument("--patch", required=True, help='Patch JSON file path, or "-" for stdin')
    p_apply.set_defaults(func=cmd_apply)

    p_export = sub.add_parser("export", help="Export final iOS spec tree (merging decisions)")
    p_export.add_argument("--state", default=DEFAULT_STATE_PATH)
    p_export.add_argument("--pretty", action="store_true")
    p_export.add_argument("--no-absorb", action="store_true", help="Disable child-absorb postprocessing")
    p_export.add_argument("--output", default=None, help="Write output JSON to a file instead of stdout")
    p_export.set_defaults(func=cmd_export)

    p_status = sub.add_parser("status", help="Show progress info (counts + next node id)")
    p_status.add_argument("--state", default=DEFAULT_STATE_PATH)
    p_status.add_argument("--pretty", action="store_true")
    p_status.set_defaults(func=cmd_status)

    p_validate = sub.add_parser("validate", help="Validate decisions against the spec contract")
    p_validate.add_argument("--state", default=DEFAULT_STATE_PATH)
    p_validate.add_argument("--pretty", action="store_true")
    p_validate.set_defaults(func=cmd_validate)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
