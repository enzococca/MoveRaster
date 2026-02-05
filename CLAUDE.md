# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SpostaRaster is a QGIS plugin (Python) that allows users to move raster layers (and optionally associated vector layers) by clicking on the map. It modifies georeferencing directly in files (GeoTIFF geotransform or world files).

## Architecture

Single-file plugin structure in `sposta_raster.py`:

- **SpostaRaster**: Main plugin class. Manages toolbar actions, undo stack (up to 10 levels), and plugin lifecycle (initGui/unload).
- **LayerSelectorDialog**: QDialog for selecting which raster and vector layers to move together. Shows selection counts and handles "selected features only" mode.
- **MultiLayerClickTool**: QgsMapToolEmitPoint subclass that handles map clicks. Calculates offset from reference point (corner or center), saves state for undo, and performs the actual move operations.

## Key Technical Details

**Raster Movement**:
- GeoTIFF: Uses GDAL to modify internal geotransform (`gdal.GA_Update`)
- World files: Writes new `.pgw`/`.jgw`/`.tfw` files based on image extension
- Layers must be removed and reloaded after file modification

**Vector Movement**:
- Uses `QgsGeometry.translate()` on all or selected features
- Manages edit state (`startEditing`/`commitChanges`)

**State for Undo**:
- Raster: stores original geotransform or world file content
- Vector: stores WKT of all affected geometries keyed by feature ID

## Development

**Testing**: Requires running within QGIS Python environment. No standalone test suite. Test by:
1. Reload plugin in QGIS: `Plugins > Plugin Reloader`
2. Add test raster/vector layers to project
3. Use plugin UI to verify behavior

**Resources**: `resources.py` is auto-generated from `resources.qrc` using `pyrcc5`. Do not edit directly.

**Debugging**: Use QGIS Python console or `qgis.utils.iface.messageBar().pushMessage()` for user feedback.

## Language

UI strings and comments are in Italian. Maintain this convention.
