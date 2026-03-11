# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

QGIS MCP (v0.1.1) connects QGIS to Claude AI through the Model Context Protocol (MCP), enabling Claude to directly control QGIS via socket-based communication.

## Architecture

The system has two components that communicate over a TCP socket (default `localhost:9876`, configurable via env vars):

1. **QGIS Plugin** (`qgis_mcp_plugin/plugin.py`) — Runs inside QGIS (3.28–4.x). A `QgisMCPServer` class creates a non-blocking TCP socket server using a `QTimer` (25ms poll interval) to accept connections and process JSON commands within QGIS's event loop. Includes a `QgisMCPDockWidget` UI for start/stop control, and `QgisMCPPlugin` as the standard QGIS plugin entry point (`classFactory`). All command handlers live in this file. A companion `compat.py` module provides enum compatibility between QGIS 3.x and 4.x (see below).

2. **MCP Server** (`src/qgis_mcp/server.py`) — Runs outside QGIS as a standalone Python process. Uses `FastMCP` from the `mcp` library to expose QGIS operations as MCP tools, resources, and prompts. A `_send()` helper unwraps the response envelope and raises on errors. All 50 tools are `async` with `title=` for human-readable names. Uses `ToolAnnotations` for read-only/destructive/idempotent hints. Long-running tools use `ctx.info()` for MCP logging. Destructive tools use `ctx.elicit()` for user confirmation (with graceful fallback).

**Data flow:** Claude → MCP Server (FastMCP) → TCP socket → QGIS Plugin (QTimer loop) → PyQGIS API → response back through socket.

There is also a standalone socket client at `src/qgis_mcp/client.py` (`QgisMCPClient` class) used for direct testing without MCP.

## Commands

```bash
# Run the MCP server (how Claude Desktop launches it)
uv run --no-sync src/qgis_mcp/server.py

# Run with custom host/port
QGIS_MCP_HOST=192.168.1.100 QGIS_MCP_PORT=9877 uv run --no-sync src/qgis_mcp/server.py

# Run with streamable HTTP transport (for remote/multi-client)
QGIS_MCP_TRANSPORT=streamable-http uv run --no-sync src/qgis_mcp/server.py

# Run unit tests (no QGIS needed - mocked socket)
uv run --no-sync pytest tests/test_mcp_tools.py -v

# Run integration tests (requires QGIS plugin server running on localhost:9876)
uv run --no-sync pytest tests/test_qgis_live.py -v

# Run all tests
uv run --no-sync pytest tests/ -v
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `QGIS_MCP_HOST` | `localhost` | Host for QGIS plugin socket connection |
| `QGIS_MCP_PORT` | `9876` | Port for QGIS plugin socket connection |
| `QGIS_MCP_TRANSPORT` | `stdio` | MCP transport: `stdio` or `streamable-http` |

## MCP Tools (50 total)

| Tool | Title | Annotations | Description |
|---|---|---|---|
| `ping` | Ping | readOnly | Check server connectivity |
| `get_qgis_info` | Get QGIS Info | readOnly | QGIS version, profile, plugins |
| `get_project_info` | Get Project Info | readOnly | Project metadata, CRS, layers |
| `load_project` | Load Project | — | Load a .qgs/.qgz file |
| `create_new_project` | Create New Project | — | Create and save new project |
| `save_project` | Save Project | idempotent | Save project to current or new path |
| `get_layers` | Get Layers | readOnly | List layers with pagination (limit/offset) |
| `add_vector_layer` | Add Vector Layer | — | Add vector layer (shapefile, GeoJSON, etc.) |
| `add_raster_layer` | Add Raster Layer | — | Add raster layer (GeoTIFF, etc.) |
| `remove_layer` | Remove Layer | destructive | Remove layer by ID (elicitation) |
| `find_layer` | Find Layer | readOnly | Find layers by name pattern (fnmatch/substring) |
| `create_memory_layer` | Create Memory Layer | — | Create in-memory vector layer with fields |
| `set_layer_visibility` | Set Layer Visibility | idempotent | Show/hide layer in layer tree |
| `zoom_to_layer` | Zoom to Layer | idempotent | Zoom canvas to layer extent |
| `get_layer_features` | Get Layer Features | readOnly | Flat features with _fid, expression filter, limit/offset, geometry |
| `get_field_statistics` | Get Field Statistics | readOnly | Aggregate stats for a field (count, mean, min, max, etc.) |
| `add_features` | Add Features | destructive | Add features to a vector layer |
| `update_features` | Update Features | destructive | Update feature attributes by fid |
| `delete_features` | Delete Features | destructive | Delete features by fids or expression (elicitation) |
| `select_features` | Select Features | idempotent | Select features by expression or fids |
| `get_selection` | Get Selection | readOnly | Get selected feature IDs and count |
| `clear_selection` | Clear Selection | idempotent | Clear layer selection |
| `set_layer_style` | Set Layer Style | — | Apply single/categorized/graduated symbology |
| `get_canvas_extent` | Get Canvas Extent | readOnly | Current map canvas extent and CRS |
| `set_canvas_extent` | Set Canvas Extent | idempotent | Set canvas extent with optional CRS transform |
| `get_canvas_screenshot` | Get Canvas Screenshot | readOnly | Fast canvas widget grab (no re-render), inline image |
| `get_raster_info` | Get Raster Info | readOnly | Raster band count, stats, nodata, dimensions |
| `execute_processing` | Execute Processing | — | Run QGIS Processing algorithm (60s, async+progress+logging) |
| `list_processing_algorithms` | List Processing Algorithms | readOnly | Search algorithms by keyword/provider |
| `get_algorithm_help` | Get Algorithm Help | readOnly | Algorithm parameters, outputs, description |
| `render_map` | Render Map | idempotent | Render canvas to inline image (60s, async+progress+logging) |
| `execute_code` | Execute Code | destructive | Run arbitrary PyQGIS code (60s, async+progress+logging) |
| `batch_commands` | Batch Commands | — | Multiple commands in one round-trip |
| `list_layouts` | List Layouts | readOnly | List print layouts |
| `export_layout` | Export Layout | idempotent | Export print layout to PDF/PNG/SVG |
| `get_message_log` | Get Message Log | readOnly | Get QGIS message log entries, filter by level/tag |
| `list_plugins` | List Plugins | readOnly | List installed plugins with enabled status |
| `get_plugin_info` | Get Plugin Info | readOnly | Detailed plugin info (version, author, path) |
| `reload_plugin` | Reload Plugin | destructive | Reload a plugin (blocks self-reload, logging) |
| `get_layer_tree` | Get Layer Tree | readOnly | Recursive layer tree with groups and layers |
| `create_layer_group` | Create Layer Group | — | Create a group in the layer tree |
| `move_layer_to_group` | Move Layer to Group | — | Move a layer into a group |
| `set_layer_property` | Set Layer Property | idempotent | Set opacity, name, scale visibility, min/max scale |
| `get_layer_extent` | Get Layer Extent | readOnly | Layer bounding box and CRS |
| `get_project_variables` | Get Project Variables | readOnly | Project-level variables (key-value) |
| `set_project_variable` | Set Project Variable | idempotent | Set a project variable (@key in expressions) |
| `validate_expression` | Validate Expression | readOnly | Validate QGIS expression, get referenced columns |
| `get_setting` | Get Setting | readOnly | Read a QGIS setting by key path |
| `set_setting` | Set Setting | destructive | Write a QGIS setting (elicitation) |
| `transform_coordinates` | Transform Coordinates | readOnly | CRS transform for points, point lists, or bboxes |

## MCP Resources

| URI | Description |
|---|---|
| `qgis://info` | QGIS version, profile, plugin count |
| `qgis://project` | Current project metadata |
| `qgis://layers` | All layers summary |
| `qgis://layers/{layer_id}/info` | Detailed layer info (CRS, extent, fields, source) |
| `qgis://layers/{layer_id}/features` | Sample features (first 10) |
| `qgis://layers/{layer_id}/schema` | Field names, types, lengths |
| `qgis://llms.txt` | LLM context: tool categories, usage patterns, quick start guide |

## MCP Prompts

| Prompt | Description |
|---|---|
| `analyze_layer` | Inspect schema, sample data, compute statistics |
| `spatial_analysis` | Spatial operation between two layers with CRS check |
| `style_map` | Create thematic map with symbology (now uses set_layer_style tool) |

## MCP Protocol Features

- **MCP Logging**: Long-running tools (`execute_processing`, `render_map`, `execute_code`) and notable operations (`load_project`, `reload_plugin`) send `ctx.info()` status messages to the client.
- **Elicitation**: Destructive tools (`remove_layer`, `delete_features`, `set_setting`) ask for user confirmation via `ctx.elicit()`. Falls back gracefully if the client doesn't support it.
- **Completions**: `layer_id` arguments support auto-completion from available layers.
- **Tool Titles**: All 50 tools have human-readable `title=` for better display in Claude Desktop / Cursor.
- **Tool Annotations**: `readOnly`, `destructive`, `idempotent` hints via `ToolAnnotations`.
- **Streamable HTTP**: Set `QGIS_MCP_TRANSPORT=streamable-http` for remote/multi-client support.

## Key Details

- **Python version**: 3.12
- **Package manager**: uv (pyproject.toml based)
- **Main dependency**: `mcp[cli]>=1.20.0` (v1.26.0 installed)
- **Dev dependencies**: `pytest>=7.0`, `pytest-asyncio>=0.23`
- **Socket protocol**: Length-prefixed framing over TCP. Each message: 4-byte big-endian uint32 length header + JSON payload bytes. Client sends `{"type": "<command>", "params": {...}}`, server responds `{"status": "success"|"error", "result": ...}`.
- **Connection management**: MCP server validates connection via `getpeername()`. Host/port configurable via `QGIS_MCP_HOST`/`QGIS_MCP_PORT` env vars. QGIS plugin accepts one client at a time.
- **All tools async**: Every tool function is `async def` to enable `await ctx.info()`, `ctx.elicit()`, etc. The `_send()` helper stays synchronous (blocking socket call — acceptable since responses are fast).
- **Feature format**: Flat dicts with `_fid` (feature ID) and attributes at top level. Geometry in `_geometry` key when requested.
- **`get_layer_features` limit**: MCP tool caps at 50 features (default 10). Supports `expression` for server-side filtering.
- **Batch support**: `batch` command type executes multiple commands in sequence, returns array of results.
- **Configurable timeouts**: `execute_processing`, `render_map`, `execute_code` use 60s; others default to 30s.
- **render_map**: Returns inline `ImageContent` (base64 PNG) so Claude can see the map directly. Optional `path` param also saves to disk.
- **get_canvas_screenshot**: Fast canvas widget grab via `QWidget.grab()` — no re-render, returns inline `ImageContent`.
- **transform_coordinates**: Uses `QgsCoordinateTransform` for point(s) and bbox CRS conversion.
- **Token optimizations**: Tools return dicts (no double JSON serialization). Plugin strips redundant metadata from responses. Features use flat format.
- **Message log capture**: Plugin connects to `QgsApplication.messageLog().messageReceived` on start, stores up to 1000 entries in a `deque`. Disconnects on stop.
- **QGIS 3.x/4.x compat**: `qgis_mcp_plugin/compat.py` resolves deprecated enum forms at import time via try/except (e.g. `QgsMapLayer.VectorLayer` → `Qgis.LayerType.Vector`). The plugin imports constants like `LAYER_VECTOR`, `MSG_WARNING`, `AGG_COUNT` from `compat` instead of using raw enum values. When adding new enum usages, add the compat constant to `compat.py` first.

## Version Management

**Two version files must be kept in sync** when bumping the version:
- `pyproject.toml` → `version = "X.Y.Z"` (MCP server / package version)
- `qgis_mcp_plugin/metadata.txt` → `version=X.Y.Z` (QGIS plugin repository version)

The QGIS plugin repository rejects uploads if the version already exists, so always bump both files together.

## Plugin Installation

The `qgis_mcp_plugin/` folder must be copied or symlinked into the QGIS profile's `python/plugins/` directory. After QGIS restart, enable via Plugins menu.
