# Figma to iOS Mapping Rules

Use BFS to process nodes, but keep architecture as the original tree when presenting output.

## 1) Component Mapping
Apply in order, but treat outputs as editable suggestions.

1. If parent maps to `UICollectionView`, map child to `UICollectionViewCell`.
2. If parent maps to `UITableView`, map child to `UITableViewCell`.
3. If parent maps to `UIButton`, child should be absorbed into parent as button content:
   - label-like child -> `UILabel`
   - icon-like child -> `UIImageView`
4. If name/type suggests text, map to `UILabel`.
5. If name/type suggests icon, map to `UIImageView`.
6. If name suggests button:
   - if direct children are simple (at most one label + at most one icon), map to `UIButton`.
   - otherwise map to a container (typically `UIView` / custom view).
7. Default to container view (`UIView`).

Do not enforce a closed enum. Human/model may override with better component choice.

## 2) Layout Strategy (Pins)
Represent layout using concrete `pins` instead of coarse labels.

Recommended shape:
```json
{
  "layout_pins": {
    "pins": {
      "left": 16,
      "right": 16,
      "top": 10,
      "bottom": null,
      "centerX": null,
      "centerY": null,
      "width": "FILL",
      "height": "intrinsicContentSize"
    },
    "notes": ["Position is driven by parent auto layout."]
  }
}
```

For cell nodes, include pin-level sizing hints such as `cellSizing: self_sizing | fixed_size`.

## 3) Visual Style Extraction
Treat script output as reference hints, not a fixed property schema.

- The tracker may provide fields like `backgroundColor`, `cornerRadius`, `stroke`, `text`, etc.
- Color and typography tokens should prefer Figma variable names first:
  - Text: prefer `textVariableName`; fallback to raw font/text values.
  - Color: prefer `colorVariableName` from fills/strokes/effects; fallback to raw color values (`hexRGBA`, RGBA, etc.).
- For per-corner radius, prefer raw corner fields when present:
  - `topLeftRadius`, `topRightRadius`, `bottomLeftRadius`, `bottomRightRadius`
  - fallback to single `cornerRadius` when needed
- In final spec, the model should choose the most useful fields for that specific component.
- Do not force irrelevant properties (for example, no text block on a container view).
- Prefer concise, implementation-oriented descriptions over exhaustive fixed keys.

## 4) Human Review Quality
Spec should be implementation-ready:
- clear tree architecture
- concrete mapping table (component + pins + style)
- per-node mapping details that developers can directly translate to UIKit code
