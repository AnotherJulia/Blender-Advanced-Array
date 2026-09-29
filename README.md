# Blender Advanced Array

A Blender extension that combines the new **Array** modifier with the features only the **Array (Legacy)** modifier has.

It adds one live, editable modifier built on Blender's own Array node group, so you keep shapes (line, circle, curve), count methods, offset methods, object transforms, randomization and merge, and gain:

- **UV offset per copy**, plus an optional random UV offset per copy
- **Start / End caps**
- **Add Constant Offset**, so relative and constant offsets can be combined like the legacy array
- **Merge** that also welds the caps

Requires **Blender 5.2 or newer**.

## Install

1. Download `advanced_array-<version>.zip` (or build it, see below).
2. In Blender: **Edit → Preferences → Get Extensions**.
3. Open the dropdown at the top right and choose **Install from Disk…**, then pick the zip.
4. Select a mesh object, then **Add Modifier → Advanced Array** (or use the *Advanced Array* tab in the 3D View sidebar, `N`).

All settings are in the modifier panel.

## Build

```
src/                  the extension (blender_manifest.toml + __init__.py)
tests/                headless tests
tools/                dev helpers
```

Build the installable zip:

```bash
blender --command extension build --source-dir src --output-dir dist
```

Run the tests (from the repository root):

```bash
blender -b --factory-startup --python tests/test_advanced_array.py
```

If the extension stops building after a Blender upgrade, `tools/inspect_builtin_array.py` prints the bundled Array group's current sockets and nodes.

## Known limitations

- Caps are placed one step before the first copy and one step after the last. The legacy array aligns caps by bounding box, so they can differ slightly. With Count 1 a cap overlaps the copy.
- "Add Constant Offset" is meant for Line arrays.
- The legacy "merge first and last" option isn't implemented; regular Merge covers most cases.

## License

GPL-3.0-or-later.
