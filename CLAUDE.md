# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MoveRaster is a QGIS plugin (Python) that moves raster layers (and optionally associated vector layers) by clicking on the map. It modifies georeferencing directly in files (GeoTIFF geotransform or world files).

## Architecture

Single-file plugin in `move_raster.py` with three classes:

- **MoveRaster**: Main plugin class. Manages toolbar actions, undo stack (up to 10 levels), and plugin lifecycle (`initGui`/`unload`). Entry point via `classFactory()` in `__init__.py`.
- **LayerSelectorDialog**: QDialog for selecting raster and vector layers to move. Shows selection counts and handles "selected features only" mode.
- **MultiLayerClickTool**: QgsMapToolEmitPoint subclass that handles map clicks. Calculates offset from reference point (corner or center), saves state for undo, and performs move operations.

## Key Technical Details

**Raster Movement**:
- GeoTIFF: Uses GDAL to modify internal geotransform (`gdal.GA_Update`)
- World files: Writes `.pgw`/`.jgw`/`.tfw` files based on image extension
- Layers must be removed via `QgsProject.instance().removeMapLayer()` and reloaded via `iface.addRasterLayer()` after file modification

**Vector Movement**:
- Uses `QgsGeometry.translate()` on all or selected features
- Manages edit state (`startEditing`/`commitChanges`)

**Undo State**:
- Raster: stores original geotransform tuple or world file content string
- Vector: stores WKT of all affected geometries keyed by feature ID

## Development Commands

**Regenerate resources** (after modifying `resources.qrc` or icons):
```bash
pyrcc5 resources.qrc -o resources.py
```

**Update translations** (after adding/changing `tr()` strings):
```bash
pylupdate5 move_raster.py -ts i18n/spostaraster_it.ts i18n/spostaraster_en.ts
# Edit .ts files with Qt Linguist, then compile:
lrelease i18n/spostaraster_it.ts i18n/spostaraster_en.ts
```

**Testing**: No standalone test suite. Test within QGIS:
1. Reload plugin: `Plugins > Plugin Reloader`
2. Add test raster/vector layers
3. Verify behavior via UI

**Debugging**: Use `iface.messageBar().pushMessage()` or QGIS Python console.

## Language

UI strings use Qt translation via `tr()`. Comments and string literals are in Italian. Maintain this convention.
