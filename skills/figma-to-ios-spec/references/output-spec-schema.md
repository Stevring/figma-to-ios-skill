# Output Spec Schema (Human + LLM Readable)

This document defines the **exported design spec JSON** produced by:
- `skills/figma-to-ios-spec/scripts/figma_ios_bfs_tool.py export`

The goal of this schema is to be:
- **reviewable/editable by humans**
- **easy for LLMs to implement**
- **stable** (fields are predictable and minimal)

---

## Top-level Object

```json
{
  "uiSystem": "UIKit",
  "root": { /* NodeSpec */ }
}
```

### `uiSystem` (string, required)
Target UI framework:
- `"UIKit"`
- `"SwiftUI"`

### `root` (NodeSpec, required)
The root node of the mapped iOS component tree.

---

## NodeSpec (recursive)

```json
{
  "source": { "id": "3145:2061", "name": "HStack", "type": "FRAME" },
  "component": { "base": "UIStackView", "custom": "OptionalCustomName" },
  "layout": { /* LayoutSpec */ },
  "properties": { /* PropertiesSpec */ },
  "children": [ /* NodeSpec[] */ ]
}
```

### `source` (object, required)
Traceability back to Figma.

- `source.id` (string, required): Figma node id
- `source.name` (string, required): Figma node name
- `source.type` (string, required): Figma node type (e.g. `FRAME`, `TEXT`, `IMAGE`)

Guideline: `source` is for referencing and debugging only. Do not put layout/style logic here.

### `component` (object, required)
The chosen iOS component type.

- `component.base` (string, required): iOS base component
  - UIKit examples: `UIView`, `UIStackView`, `UILabel`, `UIImageView`, `UIButton`, `UICollectionView`, `UICollectionViewCell`, `UITableView`, `UITableViewCell`
  - SwiftUI examples: `View`, `HStack`, `VStack`, `ZStack`, `Text`, `Image`, `Button`, `List`, `ScrollView`
- `component.custom` (string, optional): the custom wrapper type name (PascalCase)
  - Example: `{ "base": "UIView", "custom": "ProfileHeaderView" }`

Guideline: `custom` is used when the node should become a named reusable component in code.

### `layout` (object, recommended)
How this node is laid out in iOS terms (stack, pins/constraints, etc.). See **LayoutSpec**.

### `properties` (object, optional)
Style/content properties for the component (background, text, font, image, etc.). See **PropertiesSpec**.

### `children` (array, required)
Child nodes in the iOS component tree.

Guideline: The exporter may remove certain children via **child absorb** rules (e.g., button label/icon).

---

## LayoutSpec

Layout is expressed in one of a few deterministic modes using `layout.kind`.

### Common fields
- `layout.kind` (string, required): one of
  - `root`      (root placement; may still have size pins)
  - `pins`      (constraint-like placement, using `layout.pins`)
  - `stack`     (container stack layout)
  - `stackItem` (child placed by a stack container)
  - `list`      (collection/list container; framework-specific semantics)
  - `scroll`    (scroll container; framework-specific semantics)

### Pins-based layout: `kind: "pins"`

```json
{ "kind": "pins", "pins": "pins=L=16:R=-16:CY=0" }
```

- `layout.pins` (string, required for `kind: "pins"`)

#### Pins string grammar
Format:
```
pins=K=V:K=V:...
```

Allowed keys:
- `L`  leading  to `parent.leading`
- `R`  trailing to `parent.trailing` (use **negative** values for inset)
- `T`  top      to `parent.top`
- `B`  bottom   to `parent.bottom` (use **negative** values for inset)
- `CX` centerX  to `parent.centerX`
- `CY` centerY  to `parent.centerY`
- `W`  width    constant (points)
- `H`  height   constant (points)

Examples:
- Pin left, centerY, fixed height:
  - `pins=L=16:CY=0:H=44`
- Full width with insets:
  - `pins=L=16:R=-16`

Notes:
- `pins` is intentionally compact so humans can scan it quickly.
- A node typically uses either `L/R` or `CX` (not all at once) unless intentionally over-constrained.

### Stack container: `kind: "stack"`

```json
{
  "kind": "stack",
  "axis": "horizontal",
  "spacing": 8,
  "padding": { "top": 8, "left": 12, "bottom": 8, "right": 12 }
}
```

- `layout.axis` (string, required): `horizontal | vertical`
- `layout.spacing` (number, optional): default `0`
- `layout.padding` (object, optional): any of `{top,left,bottom,right}` numbers; unspecified values default to `0`

Guideline:
- In UIKit this usually maps to `UIStackView` (+ container layout margins).
- In SwiftUI this maps to `HStack/VStack` + `.padding()` + spacing.

### Stack item: `kind: "stackItem"`

```json
{ "kind": "stackItem", "grow": 1, "pins": "pins=H=44" }
```

- `layout.grow` (number, optional): stack grow/fill hint (UIKit distribution / SwiftUI layout priority)
- `layout.pins` (string, optional): typically only size pins (`W`, `H`) when needed

Guideline: do not use `L/T/R/B/CX/CY` for stack items unless the item is explicitly absolute-positioned.

### Cell sizing (UIKit only)
If `component.base` is `UITableViewCell` or `UICollectionViewCell`, you must include:
- `layout.cellSizing` (string, required): `selfSizing | fixed`
- `layout.fixedSize` (object, required when `cellSizing == "fixed"`): `{ width: number, height: number }`

Examples:
```json
{ "kind": "pins", "pins": "pins=L=0:R=0", "cellSizing": "selfSizing" }
```
```json
{ "kind": "pins", "pins": "pins=W=160:H=44", "cellSizing": "fixed", "fixedSize": { "width": 160, "height": 44 } }
```

---

## PropertiesSpec

Properties are iOS-facing. Prefer semantic tokens with fallbacks.

### ColorSpec
```json
{ "token": "SystemBackground", "fallbackHex": "#FFFFFFFF" }
```

- `token` (string, optional): semantic color name
- `fallbackHex` (string, optional): `#RRGGBBAA` fallback

Guideline: ideally include both when available.

### Common view properties (UIKit + SwiftUI)
- `backgroundColor` (ColorSpec)
- `cornerRadius` (number)
- `stroke` (object): `{ width: number, color: ColorSpec }`
- `shadow` (object or array): best-effort fields:
  - `{ offsetX, offsetY, radius, color: ColorSpec, opacity? }`
- `opacity` (number)

### Text / label properties
For UIKit `UILabel` and SwiftUI `Text`:
- `text` (string)
- `textColor` (ColorSpec)
- `font` (object):
  - `{ "token": "Body" }`
  - `{ "fallback": { "name": "SFPro", "size": 16, "weight": 600 } }`
  - or both: `{ "token": "Body", "fallback": {...} }`

### Image properties
For UIKit `UIImageView` and SwiftUI `Image`:
- `image` (object): `{ "imageHash": "..." }` (plus optional metadata like `scaleMode`)
- UIKit only: `contentMode` (string)
  - common values: `scaleAspectFit`, `scaleAspectFill`, `scaleToFill`

### Button properties (child absorb output)
After export, buttons may contain:
- UIKit `UIButton`:
  - `title` (string)
  - `titleColor` (ColorSpec)
  - `titleFont` (font object)
  - `image` (image object)
  - `imageContentMode` (string, e.g. `scaleAspectFit`)
  - `titleFrom` / `imageFrom` (string source ids for traceability)
- SwiftUI `Button`:
  - `title` (string) and/or `image` (image object)
  - `titleFrom` / `imageFrom`

---

## Child Absorb (Exporter Behavior)

To reduce wrapper noise and make the spec implementable:
- UIKit:
  - `UIButton` absorbs direct `UILabel` and `UIImageView` children into button properties
  - `UIImageView` may absorb a child `UIImageView` if the parent has no image property
- SwiftUI:
  - `Button` absorbs direct `Text` / `Image`
  - `Image` may absorb a child `Image` if the parent has no image property

Absorbed children are removed from `children`, and `titleFrom` / `imageFrom` preserve traceability.

