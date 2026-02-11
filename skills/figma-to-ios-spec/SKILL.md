---
name: figma-to-ios-spec
description: Deterministic breadth-first (parent-first) mapping from a Figma node-tree JSON to a human-reviewable iOS component design spec (UIKit or SwiftUI). Use when an agent must traverse large Figma JSON under context limits; uses a local BFS helper script to query skeleton/facts, apply per-node decisions, validate, and export a pins-based layout spec.
---

# Figma -> iOS Component Design Spec (BFS + Script-Assisted)

Produce an iOS component **design spec file** that is readable for humans and LLMs, and can be reviewed/edited before implementation.

This skill is optimized for **context limits**:
- Do **not** load the full Figma JSON into the LLM context.
- Use the helper script to fetch only the next node’s skeleton + facts, BFS-style.

## Output Artifact (Design Spec)
The final artifact is a single JSON file (pretty-printed) exported by the script:
- Top-level:
  - `uiSystem`: `"UIKit"` or `"SwiftUI"`
  - `root`: `NodeSpec`
- `NodeSpec` (recursive parent-child tree):
  - `source`: `{ id, name, type }`
  - `component`: `{ base: string, custom?: string }`
  - `layout`: object (see “Layout Contract”)
  - `properties`: object (see “Properties Contract”)
  - `children`: `NodeSpec[]`

Notes:
- The spec must be **implementation-oriented** (iOS concepts), not a reprint of Figma fields.
- The spec must be **stable** and **minimal** (only what’s needed to build the view).

Field-by-field schema reference:
- `skills/figma-to-ios-spec/references/output-spec-schema.md`

## Helper Script (BFS Tool)
Script:
- `skills/figma-to-ios-spec/scripts/figma_ios_bfs_tool.py`

State file:
- Default: `.ai_tmp/figma_ios_state.json` (override with `--state`)

### Step 0 — Initialize
```bash
python3 skills/figma-to-ios-spec/scripts/figma_ios_bfs_tool.py init \
  --input <figma-node-tree.json> \
  --ui-system UIKit \
  --state .ai_tmp/figma_ios_state.json
```

For SwiftUI:
```bash
python3 skills/figma-to-ios-spec/scripts/figma_ios_bfs_tool.py init \
  --input <figma-node-tree.json> \
  --ui-system SwiftUI \
  --state .ai_tmp/figma_ios_state.json
```

### Step 1 — Start From Skeleton (Optional)
```bash
python3 skills/figma-to-ios-spec/scripts/figma_ios_bfs_tool.py skeleton \
  --state .ai_tmp/figma_ios_state.json \
  --depth 2 --pretty
```

### Step 2 — BFS Loop (Deterministic)
Repeat until `next` returns `{ "done": true }`:
```bash
python3 skills/figma-to-ios-spec/scripts/figma_ios_bfs_tool.py next --state .ai_tmp/figma_ios_state.json --pretty
```

You will receive:
- `node` (skeleton)
- `parent` + `parentDecision` (if any)
- `requirements` (parent-imposed rules)
- `facts` (minimal Figma-derived facts for mapping)
- `children` (skeleton list)
- `hints` (e.g. `componentHint`, `pinsCandidate`, `cellSizingHint`, `contentModeHint`)

Write a **decision patch** for that node and apply it:
```bash
cat patch.json | python3 skills/figma-to-ios-spec/scripts/figma_ios_bfs_tool.py apply \
  --state .ai_tmp/figma_ios_state.json \
  --patch -
```

### Step 3 — Validate
Run validation any time (recommended frequently):
```bash
python3 skills/figma-to-ios-spec/scripts/figma_ios_bfs_tool.py validate \
  --state .ai_tmp/figma_ios_state.json --pretty
```

### Step 4 — Export the Design Spec
```bash
python3 skills/figma-to-ios-spec/scripts/figma_ios_bfs_tool.py export \
  --state .ai_tmp/figma_ios_state.json \
  --pretty \
  --output ios-component-design-spec.json
```

Export defaults:
- “child absorb” postprocessing is **enabled**.
- Use `--no-absorb` only for debugging.

## Decision Patch Contract (What the LLM Writes)
Patch is JSON, either a single object or an array. Each patch targets one node:

```json
{
  "id": "<figma-node-id>",
  "component": { "base": "UIView", "custom": "OptionalCustomName" },
  "layout": { ... },
  "properties": { ... }
}
```

Only write iOS-relevant fields. Do not copy raw `facts` into decisions.

## Component Rules (BFS, Parent-First)
Always decide the component type **with parent context first**.

### Parent-imposed rules (must follow)
UIKit:
- If parent `base == "UICollectionView"` -> direct children **must** be `component.base == "UICollectionViewCell"`.
- If parent `base == "UITableView"` -> direct children **must** be `component.base == "UITableViewCell"`.
- If parent `base == "UIButton"` -> direct children should be button-contained:
  - `UILabel` (title)
  - `UIImageView` (icon)

SwiftUI (hints, but keep consistent):
- If parent `base == "List"` -> direct children should be row views (usually `component.base == "View"` with a `custom` row type).
- If parent `base == "Button"` -> direct children should be `Text` and/or `Image` (button label).

### Name/type/layout heuristics (use when parent doesn’t constrain)
Use these deterministic priorities:
1) If Figma node `type == "TEXT"` -> `UILabel` (UIKit) / `Text` (SwiftUI)
2) If Figma node `type == "IMAGE"` -> `UIImageView` / `Image`
3) If Figma node has auto-layout (`layoutMode == "HORIZONTAL"|"VERTICAL"`) -> `UIStackView` / `HStack` or `VStack`
4) If name suggests list/collection/scroll:
   - UIKit: `UITableView` or `UICollectionView`
   - SwiftUI: `List` or `ScrollView`
5) If name suggests button -> `UIButton` / `Button`
6) If name suggests label -> `UILabel` / `Text`
7) If name suggests icon/image -> `UIImageView` / `Image`
8) Default -> `UIView` / `View`

### Custom components
If a node represents a reusable unit (commonly Figma `COMPONENT` or `INSTANCE`), set `component.custom`:
- UIKit: `component.base` should still be a UIKit type (e.g. `UIView`, `UIButton`)
- SwiftUI: `component.base` should usually be `"View"`
- `custom` must be PascalCase and stable (e.g. `ProfileHeaderView`, `PrimaryButtonRow`)

## Layout Contract
Layout must be expressed using **either stack layout** or a compact **pins** string.

### 1) Pins string (constraint-like layout)
Use a single string:
```
pins=L=0:R=-6:CY=0
```

Allowed keys:
- `L` leading (to `parent.leading`)
- `R` trailing (to `parent.trailing`) — use negative for inset
- `T` top (to `parent.top`)
- `B` bottom (to `parent.bottom`) — use negative for inset
- `CX` centerX (to `parent.centerX`)
- `CY` centerY (to `parent.centerY`)
- `W` width (constant)
- `H` height (constant)

### 2) Layout fields
Write `layout` using this deterministic schema:

Common:
- `layout.kind`: one of `root | pins | stack | stackItem | list | scroll`
- If `layout.kind == "pins"`: `layout.pins` is required and must start with `pins=`.
- If `layout.kind == "stack"`: define the container layout (below).
- If `layout.kind == "stackItem"`: do not pin position; only express size constraints if needed (often `pins=W=...` and/or `pins=H=...`).

Stack containers:
```json
{
  "kind": "stack",
  "axis": "horizontal",
  "spacing": 8,
  "padding": { "top": 8, "left": 12, "bottom": 8, "right": 12 }
}
```

Stack items (children of auto-layout containers):
```json
{
  "kind": "stackItem",
  "grow": 1,
  "pins": "pins=H=44"
}
```

### Cells (UIKit) — option A
If `component.base` is `UITableViewCell` or `UICollectionViewCell`, you must set:
- `layout.cellSizing`: `"selfSizing"` or `"fixed"`
- If `"fixed"`, also set `layout.fixedSize: { width, height }`

Rule:
- If Figma sizing is HUG on width or height -> use `"selfSizing"`
- Else -> use `"fixed"` and compute from frame size

## Properties Contract
Prefer semantic tokens; keep a fallback value to ensure implementability.

### Color spec
```json
{ "token": "SystemBackground", "fallbackHex": "#FFFFFFFF" }
```

### Common view properties
- `backgroundColor`: Color spec
- `cornerRadius`: number
- `stroke`: `{ width: number, color: ColorSpec }`
- `shadow`: `{ offsetX, offsetY, radius, color: ColorSpec, opacity? }` (or an array if multiple)
- `opacity`: number

### UILabel / Text
- `text`: string
- `textColor`: Color spec
- `font`: `{ token?: string, fallback?: { name: string, size: number, weight?: number } }`

### UIImageView / Image
- `image`: `{ imageHash: string }` (optionally include `scaleMode`)
- UIKit only: `contentMode` (use hints from the tool; e.g. `scaleAspectFit`, `scaleAspectFill`)

### UIButton / Button
Do not keep child label/image as separate nodes in the final exported spec.

Instead, let the exporter’s **child absorb** postprocess do this:
- For UIKit `UIButton`: absorb direct `UILabel`/`UIImageView` children into:
  - `title`, `titleColor`, `titleFont`, `image`
- For SwiftUI `Button`: absorb direct `Text`/`Image` children into:
  - `title`, `image`

Traceability:
- The exporter records `titleFrom` / `imageFrom` with absorbed child source ids when available.
