# Agents Guide: figma-to-ios-spec

## What This Repo Provides
- A publishable Codex skill at `skills/figma-to-ios-spec/` that guides an agent to convert a **large Figma node-tree JSON** into a **human-reviewable iOS component design spec**.
- A deterministic BFS helper script (`figma_ios_bfs_tool.py`) that lets the agent traverse the tree **breadth-first (parent-first)** without loading the full JSON into context.

## Key Files
- Skill entrypoint: `skills/figma-to-ios-spec/SKILL.md`
- Output schema reference: `skills/figma-to-ios-spec/references/output-spec-schema.md`
- BFS helper script: `skills/figma-to-ios-spec/scripts/figma_ios_bfs_tool.py`
- Default state location: `.ai_tmp/figma_ios_state.json` (can override with `--state`)

## High-Level Workflow (BFS / Parent-First)
1) Initialize state from a Figma node-tree JSON.
2) Repeatedly call `next` to fetch a compact payload for the next undecided node (includes parent decision + minimal facts).
3) The agent writes a per-node decision patch (component/layout/properties) and applies it via `apply`.
4) Validate decisions with `validate`.
5) Export the final iOS component design spec tree with `export`.

## Quickstart Commands
UIKit:
```bash
python3 skills/figma-to-ios-spec/scripts/figma_ios_bfs_tool.py init \
  --input <figma.json> --ui-system UIKit --state .ai_tmp/state.json

python3 skills/figma-to-ios-spec/scripts/figma_ios_bfs_tool.py next --state .ai_tmp/state.json --pretty
# create patch.json
python3 skills/figma-to-ios-spec/scripts/figma_ios_bfs_tool.py apply --state .ai_tmp/state.json --patch patch.json

python3 skills/figma-to-ios-spec/scripts/figma_ios_bfs_tool.py validate --state .ai_tmp/state.json --pretty

python3 skills/figma-to-ios-spec/scripts/figma_ios_bfs_tool.py export \
  --state .ai_tmp/state.json --pretty --output ios-component-design-spec.json
```

SwiftUI:
```bash
python3 skills/figma-to-ios-spec/scripts/figma_ios_bfs_tool.py init \
  --input <figma.json> --ui-system SwiftUI --state .ai_tmp/state.json
```

## Output Format Notes
- Exported spec is JSON and keeps Unicode readable (no `\\uXXXX` escaping).
- Layout uses a compact `pins=...` string (example: `pins=L=0:R=-6:CY=0`).
- UIKit cell sizing uses Option A:
  - `layout.cellSizing`: `selfSizing | fixed`
  - if `fixed`, require `layout.fixedSize: {width,height}`
- Export does “child absorb” by default:
  - UIKit: `UIButton` absorbs direct `UILabel`/`UIImageView` into `title`/`image` fields; `UIImageView` can absorb child image.
  - SwiftUI: `Button` absorbs direct `Text`/`Image`.

## Validation
Run:
```bash
python3 skills/figma-to-ios-spec/scripts/figma_ios_bfs_tool.py validate --state .ai_tmp/state.json --pretty
```
It checks (best-effort):
- required decision shape (`component.base`, valid `layout.pins` grammar when present)
- UIKit parent/child rules (collection/table -> cell)
- cell sizing requirements

## Where The “Determinism” Lives
- BFS ordering + tree indexing + minimal facts extraction: `figma_ios_bfs_tool.py`
- Mapping rules, patch contract, and layout/property semantics: `skills/figma-to-ios-spec/SKILL.md`
- Field-by-field schema reference for human review: `skills/figma-to-ios-spec/references/output-spec-schema.md`

