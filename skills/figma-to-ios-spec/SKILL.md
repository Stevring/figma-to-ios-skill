---
name: figma-to-ios-spec
description: "Map Figma node JSON to a human-editable iOS component spec using a simplified workflow: read JSON skeleton first, then map nodes incrementally and write results into a temporary markdown spec file. Use when Codex needs structured node context plus parent-aware decisions during Figma-to-UIKit mapping."
---

# Figma to iOS Spec

## Overview
Convert Figma node JSON into a reviewable iOS component spec with a markdown-first workflow.

Use `scripts/json_reader.py` for context lookup. Keep final mapping results in one temporary markdown file that is updated during traversal.

## Input
- A JSON object with a root Figma node and nested `children`.
- Example: `assets/input-example/collection-view-cell.json`

## Core Principles
- Read full skeleton once at initialization for global awareness.
- Traverse and decide mapping in BFS order.
- Persist mapping decisions immediately to a markdown spec file inside one session temp folder.
- For each child decision, read parent info from the markdown spec first, and read node details from `json_reader.py`.
- For color/font fields, prefer Figma variable names (`textVariableName`, `colorVariableName`) when available; use raw values only as fallback.
- Treat script output as reference only; choose final fields and wording based on implementation clarity.
- Keep all artifacts (copied input, intermediate files, spec) in the same temp folder for that session.
- In the final response, always tell the user the temp folder path.

## Workflow
1. Initialize context from JSON skeleton.
2. Build and maintain a TODO list for unmapped nodes.
3. Map nodes in BFS batches.
4. Continuously update the temporary markdown spec file.
5. Deliver the markdown spec.

### 1) Initialize
1. Create one temp folder for the full mapping session (naming rule: `figma-ios-spec-YYYY-MM-DD-HHmmss`):
```bash
TS="$(date +"%Y-%m-%d-%H%M%S")"
BASE="/tmp/figma-ios-spec-$TS"
WORK_DIR="$BASE"
IDX=1
while [ -e "$WORK_DIR" ]; do
  WORK_DIR="${BASE}-$IDX"
  IDX=$((IDX + 1))
done
mkdir -p "$WORK_DIR"
```

2. Copy input JSON into the temp folder:
```bash
cp <figma_json_path> "$WORK_DIR/input.json"
```

3. Generate skeleton:
```bash
python3 scripts/json_reader.py skeleton \
  --input "$WORK_DIR/input.json" \
  --format markdown \
  --output "$WORK_DIR/figma-skeleton.md"
```

4. Create TODO list from skeleton (`node_id` + BFS order).
5. Initialize spec file in temp folder (for example `$WORK_DIR/ios-mapping-spec.md`) with sections:
   - Overall Architecture
   - Component Mapping Table
   - Mapping Details

### 2) Map Next Node(s)
For each pending node:
1. Read parent mapping from `$WORK_DIR/ios-mapping-spec.md`.
2. Read node details from JSON:
```bash
python3 scripts/json_reader.py node \
  --input "$WORK_DIR/input.json" \
  --node-id <node_id> \
  --format markdown
```
Or by BFS index:
```bash
python3 scripts/json_reader.py node \
  --input "$WORK_DIR/input.json" \
  --bfs-index <index> \
  --format json
```
3. Decide mapping and layout strategy.
4. Update `$WORK_DIR/ios-mapping-spec.md` immediately.
5. Mark TODO item as completed.

### 3) Process in Batches
Use BFS batch view when needed:
```bash
python3 scripts/json_reader.py batch \
  --input "$WORK_DIR/input.json" \
  --start <bfs_start> \
  --count <batch_size> \
  --format markdown
```

### 4) Finalize
1. Return the spec from `$WORK_DIR/ios-mapping-spec.md`.
2. In the final response, include:
   - temp folder path (`$WORK_DIR`)
   - spec file path (`$WORK_DIR/ios-mapping-spec.md`)

## Expected Output Sections
The generated spec must contain:
1. Overall Architecture
2. Component Mapping Table
3. Mapping Details

## Resources
- Rules: `references/mapping-rules.md`
- Contract schema: `references/mapping-contract.schema.json`
- Spec template: `assets/ios-component-spec-template.md`
- Contract example: `assets/mapping-contract-template.json`
- Decisions example: `assets/mapping-decisions-template.json`
