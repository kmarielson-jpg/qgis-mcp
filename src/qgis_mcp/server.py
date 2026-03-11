#!/usr/bin/env python3
"""
QGIS MCP Server - Exposes QGIS operations as MCP tools, resources, and prompts.
"""

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.prompts.base import UserMessage
from mcp.types import (
    Completion,
    CompletionArgument,
    ImageContent,
    TextContent,
    ToolAnnotations,
)

from qgis_mcp.client import QgisMCPClient

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("QgisMCPServer")


# ---------------------------------------------------------------------------
# Persistent connection management
# ---------------------------------------------------------------------------

_qgis_connection: QgisMCPClient | None = None
_connection_validated_at: float = 0.0
_CONNECTION_TTL: float = 5.0  # seconds between getpeername() validations


def get_qgis_connection() -> QgisMCPClient:
    """Get or create a persistent QGIS connection.

    Uses a TTL cache for connection validation: getpeername() is only
    called at most once per _CONNECTION_TTL seconds, avoiding a syscall
    on every tool invocation.
    """
    global _qgis_connection, _connection_validated_at

    if _qgis_connection is not None:
        now = time.monotonic()
        if now - _connection_validated_at < _CONNECTION_TTL:
            return _qgis_connection
        try:
            _qgis_connection.socket.getpeername()
            _connection_validated_at = now
            return _qgis_connection
        except Exception:
            logger.warning("Existing connection is no longer valid, reconnecting")
            with contextlib.suppress(Exception):
                _qgis_connection.disconnect()
            _qgis_connection = None
            _connection_validated_at = 0.0

    host = os.environ.get("QGIS_MCP_HOST", "localhost")
    port = int(os.environ.get("QGIS_MCP_PORT", "9876"))
    _qgis_connection = QgisMCPClient(host=host, port=port)
    if not _qgis_connection.connect():
        _qgis_connection = None
        raise ConnectionError("Could not connect to QGIS. Make sure the QGIS plugin is running.")
    _connection_validated_at = time.monotonic()
    logger.info(f"Created new persistent connection to QGIS at {host}:{port}")
    return _qgis_connection


# ---------------------------------------------------------------------------
# Helper: send command, unwrap envelope, raise on error
# ---------------------------------------------------------------------------


def _send_sync(command_type: str, params: dict | None = None, timeout: int = 30) -> dict:
    """Send a command synchronously and return the unwrapped result."""
    qgis = get_qgis_connection()
    result = qgis.send_command(command_type, params, timeout=timeout)
    if not result or result.get("status") == "error":
        raise RuntimeError(result.get("message", "Command failed") if result else "No response")
    return result.get("result", {})


async def _send(command_type: str, params: dict | None = None, timeout: int = 30) -> dict:
    """Send a command via asyncio.to_thread to avoid blocking the event loop."""
    return await asyncio.to_thread(_send_sync, command_type, params, timeout)


# ---------------------------------------------------------------------------
# Helper: elicit confirmation for destructive operations
# ---------------------------------------------------------------------------


async def _confirm_destructive(ctx: Context, message: str) -> bool:
    """Ask user for confirmation before destructive operation.

    Returns True if confirmed or if client doesn't support elicitation.
    """
    try:
        response = await ctx.elicit(
            message=message,
            schema={
                "type": "object",
                "properties": {
                    "confirm": {
                        "type": "boolean",
                        "description": "Confirm this operation",
                    },
                },
                "required": ["confirm"],
            },
        )
        return response.action == "accept" and bool(response.data.get("confirm"))
    except Exception:
        # Client doesn't support elicitation — proceed without confirmation
        return True


# ---------------------------------------------------------------------------
# Server lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Manage server startup and shutdown lifecycle."""
    try:
        logger.info("QgisMCPServer starting up")
        try:
            get_qgis_connection()
            logger.info("Successfully connected to QGIS on startup")
        except Exception as e:
            logger.warning(f"Could not connect to QGIS on startup: {e}")
        yield {}
    finally:
        global _qgis_connection, _connection_validated_at
        if _qgis_connection:
            logger.info("Disconnecting from QGIS on shutdown")
            _qgis_connection.disconnect()
            _qgis_connection = None
            _connection_validated_at = 0.0
        logger.info("QgisMCPServer shut down")


mcp = FastMCP(
    name="Qgis_mcp",
    instructions="QGIS integration through the Model Context Protocol. "
    "Use tools for actions, resources for read-only data, prompts for workflows.",
    lifespan=server_lifespan,
)


# ===========================================================================
# MCP TOOLS (50 total)
# ===========================================================================

# --- Connectivity & Info ---


@mcp.tool(
    title="Ping",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Check connectivity to the QGIS plugin server. Returns pong if connected.",
)
async def ping(ctx: Context) -> dict:
    return await _send("ping")


@mcp.tool(
    title="Get QGIS Info",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Get QGIS version, profile path, and plugin count.",
)
async def get_qgis_info(ctx: Context) -> dict:
    return await _send("get_qgis_info")


@mcp.tool(
    title="Get Project Info",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Get current project metadata: filename, title, CRS, layer count, and summary of layers.",
)
async def get_project_info(ctx: Context) -> dict:
    return await _send("get_project_info")


# --- Project Management ---


@mcp.tool(title="Load Project", description="Load a QGIS project from a .qgs/.qgz file path.")
async def load_project(ctx: Context, path: str) -> dict:
    await ctx.info(f"Loading project: {path}")
    return await _send("load_project", {"path": path})


@mcp.tool(
    title="Create New Project",
    description="Create a new empty QGIS project and save it to the given path.",
)
async def create_new_project(ctx: Context, path: str) -> dict:
    return await _send("create_new_project", {"path": path})


@mcp.tool(
    title="Save Project",
    annotations=ToolAnnotations(idempotentHint=True),
    description="Save the current project. Optionally specify a new path.",
)
async def save_project(ctx: Context, path: str | None = None) -> dict:
    params = {}
    if path:
        params["path"] = path
    return await _send("save_project", params)


# --- Layer Management ---


@mcp.tool(
    title="Get Layers",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="List layers in the current project with IDs, names, types, visibility, and type-specific info. "
    "Use limit/offset for pagination. Response includes total_count.",
)
async def get_layers(ctx: Context, limit: int = 50, offset: int = 0) -> dict:
    return await _send("get_layers", {"limit": limit, "offset": offset})


@mcp.tool(
    title="Add Vector Layer",
    description="Add a vector layer (shapefile, GeoJSON, GeoPackage, etc.) to the project.",
)
async def add_vector_layer(
    ctx: Context, path: str, provider: str = "ogr", name: str | None = None
) -> dict:
    params = {"path": path, "provider": provider}
    if name:
        params["name"] = name
    return await _send("add_vector_layer", params)


@mcp.tool(
    title="Add Raster Layer", description="Add a raster layer (GeoTIFF, etc.) to the project."
)
async def add_raster_layer(
    ctx: Context, path: str, provider: str = "gdal", name: str | None = None
) -> dict:
    params = {"path": path, "provider": provider}
    if name:
        params["name"] = name
    return await _send("add_raster_layer", params)


@mcp.tool(
    title="Remove Layer",
    annotations=ToolAnnotations(destructiveHint=True),
    description="Remove a layer from the project by its layer ID. This is irreversible.",
)
async def remove_layer(ctx: Context, layer_id: str) -> dict:
    if not await _confirm_destructive(ctx, f"Remove layer {layer_id}? This cannot be undone."):
        return {"ok": False, "message": "Cancelled by user"}
    return await _send("remove_layer", {"layer_id": layer_id})


@mcp.tool(
    title="Find Layer",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Find layers by name pattern. Supports fnmatch wildcards (e.g. 'roads*') "
    "and substring matching.",
)
async def find_layer(ctx: Context, name_pattern: str) -> dict:
    return await _send("find_layer", {"name_pattern": name_pattern})


@mcp.tool(
    title="Create Memory Layer",
    description="Create a new in-memory vector layer. geometry_type: Point, LineString, Polygon, "
    "MultiPoint, MultiLineString, MultiPolygon. fields: [{name, type}] where "
    "type is integer, double, string, date, datetime.",
)
async def create_memory_layer(
    ctx: Context,
    name: str,
    geometry_type: str,
    crs: str = "EPSG:4326",
    fields: list[dict] | None = None,
) -> dict:
    params = {"name": name, "geometry_type": geometry_type, "crs": crs}
    if fields:
        params["fields"] = fields
    return await _send("create_memory_layer", params)


# --- Layer Visibility & Navigation ---


@mcp.tool(
    title="Set Layer Visibility",
    annotations=ToolAnnotations(idempotentHint=True),
    description="Set a layer's visibility in the layer tree (show/hide on map).",
)
async def set_layer_visibility(ctx: Context, layer_id: str, visible: bool) -> dict:
    return await _send("set_layer_visibility", {"layer_id": layer_id, "visible": visible})


@mcp.tool(
    title="Zoom to Layer",
    annotations=ToolAnnotations(idempotentHint=True),
    description="Zoom the map canvas to the full extent of the specified layer.",
)
async def zoom_to_layer(ctx: Context, layer_id: str) -> dict:
    return await _send("zoom_to_layer", {"layer_id": layer_id})


# --- Feature Access ---


@mcp.tool(
    title="Get Layer Features",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Retrieve features from a vector layer. Features are flat dicts with _fid and attributes "
    "at top level. Supports expression filtering (QGIS expressions like "
    '"name = \'Berlin\'" or "population > 1000000"), limit (max 50, default 10), offset for paging, '
    "and optional geometry inclusion (in _geometry key).",
)
async def get_layer_features(
    ctx: Context,
    layer_id: str,
    limit: int = 10,
    offset: int = 0,
    expression: str | None = None,
    include_geometry: bool = False,
) -> dict:
    if limit > 50:
        limit = 50
    params = {
        "layer_id": layer_id,
        "limit": limit,
        "offset": offset,
        "include_geometry": include_geometry,
    }
    if expression:
        params["expression"] = expression
    return await _send("get_layer_features", params)


@mcp.tool(
    title="Get Field Statistics",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Compute aggregate statistics (count, sum, mean, min, max, stdev) for a numeric field. "
    "For non-numeric fields returns count and distinct values.",
)
async def get_field_statistics(ctx: Context, layer_id: str, field_name: str) -> dict:
    return await _send("get_field_statistics", {"layer_id": layer_id, "field_name": field_name})


# --- Feature Editing ---


@mcp.tool(
    title="Add Features",
    annotations=ToolAnnotations(destructiveHint=True),
    description="Add features to a vector layer. Each feature: {attributes: {field: value}, "
    "geometry_wkt: 'POINT(1 2)'}. Returns count of added features.",
)
async def add_features(ctx: Context, layer_id: str, features: list[dict]) -> dict:
    return await _send("add_features", {"layer_id": layer_id, "features": features})


@mcp.tool(
    title="Update Features",
    annotations=ToolAnnotations(destructiveHint=True),
    description="Update feature attributes. updates: [{fid: 1, attributes: {field: value}}]. "
    "Returns count of updated features.",
)
async def update_features(ctx: Context, layer_id: str, updates: list[dict]) -> dict:
    return await _send("update_features", {"layer_id": layer_id, "updates": updates})


@mcp.tool(
    title="Delete Features",
    annotations=ToolAnnotations(destructiveHint=True),
    description="Delete features by feature IDs or expression filter. "
    "Provide either fids (list of ints) or expression (string), not both.",
)
async def delete_features(
    ctx: Context,
    layer_id: str,
    fids: list[int] | None = None,
    expression: str | None = None,
) -> dict:
    target = f"fids={fids}" if fids else f"expression='{expression}'"
    if not await _confirm_destructive(ctx, f"Delete features from layer {layer_id} ({target})?"):
        return {"ok": False, "message": "Cancelled by user"}
    params = {"layer_id": layer_id}
    if fids is not None:
        params["fids"] = fids
    if expression:
        params["expression"] = expression
    return await _send("delete_features", params)


# --- Selection ---


@mcp.tool(
    title="Select Features",
    annotations=ToolAnnotations(idempotentHint=True),
    description="Select features in a layer by expression or feature IDs.",
)
async def select_features(
    ctx: Context,
    layer_id: str,
    expression: str | None = None,
    fids: list[int] | None = None,
) -> dict:
    params = {"layer_id": layer_id}
    if expression:
        params["expression"] = expression
    if fids is not None:
        params["fids"] = fids
    return await _send("select_features", params)


@mcp.tool(
    title="Get Selection",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Get the current selection for a layer. Returns feature IDs and count.",
)
async def get_selection(ctx: Context, layer_id: str) -> dict:
    return await _send("get_selection", {"layer_id": layer_id})


@mcp.tool(
    title="Clear Selection",
    annotations=ToolAnnotations(idempotentHint=True),
    description="Clear the selection on a layer.",
)
async def clear_selection(ctx: Context, layer_id: str) -> dict:
    return await _send("clear_selection", {"layer_id": layer_id})


# --- Symbology ---


@mcp.tool(
    title="Set Layer Style",
    description="Set layer symbology. style_type: 'single' (one symbol), 'categorized' (unique values), "
    "or 'graduated' (numeric ranges). field is required for categorized/graduated. "
    "color_ramp: name from QGIS style (e.g. 'Spectral', 'Viridis', 'Blues'). "
    "classes: number of classes for graduated (default 5).",
)
async def set_layer_style(
    ctx: Context,
    layer_id: str,
    style_type: str,
    field: str | None = None,
    classes: int = 5,
    color_ramp: str = "Spectral",
) -> dict:
    params = {
        "layer_id": layer_id,
        "style_type": style_type,
        "classes": classes,
        "color_ramp": color_ramp,
    }
    if field:
        params["field"] = field
    return await _send("set_layer_style", params)


# --- Canvas ---


@mcp.tool(
    title="Get Canvas Extent",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Get the current map canvas extent and CRS.",
)
async def get_canvas_extent(ctx: Context) -> dict:
    return await _send("get_canvas_extent")


@mcp.tool(
    title="Set Canvas Extent",
    annotations=ToolAnnotations(idempotentHint=True),
    description="Set the map canvas extent. Coordinates should be in the specified CRS (default: project CRS).",
)
async def set_canvas_extent(
    ctx: Context, xmin: float, ymin: float, xmax: float, ymax: float, crs: str | None = None
) -> dict:
    params = {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}
    if crs:
        params["crs"] = crs
    return await _send("set_canvas_extent", params)


@mcp.tool(
    title="Get Canvas Screenshot",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Grab a fast screenshot of the current map canvas widget (no re-render). "
    "Returns the image inline. Much faster than render_map.",
)
async def get_canvas_screenshot(ctx: Context) -> list:
    result = await _send("get_canvas_screenshot")
    return [ImageContent(type="image", data=result["base64_data"], mimeType="image/png")]


# --- Raster ---


@mcp.tool(
    title="Get Raster Info",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Get raster layer info: band count, dimensions, CRS, extent, per-band statistics, nodata values.",
)
async def get_raster_info(ctx: Context, layer_id: str) -> dict:
    return await _send("get_raster_info", {"layer_id": layer_id})


# --- Processing ---


@mcp.tool(
    title="Execute Processing",
    description="Execute a QGIS Processing algorithm. Use get_algorithm_help to discover parameters. "
    "Layer params accept layer IDs or file paths. Set OUTPUT to 'memory:' for temp layers.",
)
async def execute_processing(ctx: Context, algorithm: str, parameters: dict) -> dict:
    await ctx.info(f"Running algorithm: {algorithm}")
    await ctx.report_progress(0, 100)
    result = await _send(
        "execute_processing", {"algorithm": algorithm, "parameters": parameters}, timeout=60
    )
    await ctx.report_progress(100, 100)
    return result


@mcp.tool(
    title="List Processing Algorithms",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Search for processing algorithms by keyword and/or provider. "
    "Returns id, name, provider for each match.",
)
async def list_processing_algorithms(
    ctx: Context,
    search: str | None = None,
    provider: str | None = None,
) -> dict:
    params = {}
    if search:
        params["search"] = search
    if provider:
        params["provider"] = provider
    return await _send("list_processing_algorithms", params)


@mcp.tool(
    title="Get Algorithm Help",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Get detailed help for a processing algorithm: parameters (name, type, optional, default), "
    "outputs, and description.",
)
async def get_algorithm_help(ctx: Context, algorithm_id: str) -> dict:
    return await _send("get_algorithm_help", {"algorithm_id": algorithm_id})


# --- Rendering ---


@mcp.tool(
    title="Render Map",
    annotations=ToolAnnotations(idempotentHint=True),
    description="Render the current map canvas to an image. Returns the image inline so you can see it. "
    "Optionally saves to a file path on disk.",
)
async def render_map(
    ctx: Context, width: int = 800, height: int = 600, path: str | None = None
) -> list:
    await ctx.info("Rendering map...")
    await ctx.report_progress(0, 100)
    params = {"width": width, "height": height}
    if path:
        params["path"] = path
    result = await _send("render_map_base64", params, timeout=60)
    await ctx.report_progress(100, 100)

    content = [ImageContent(type="image", data=result["base64_data"], mimeType="image/png")]
    if path:
        content.append(
            TextContent(
                type="text", text=json.dumps({"saved": path, "width": width, "height": height})
            )
        )
    return content


# --- Code Execution ---


@mcp.tool(
    title="Execute Code",
    annotations=ToolAnnotations(destructiveHint=True),
    description="Execute arbitrary PyQGIS code. Use for operations not covered by other tools. "
    "Has access to QgsProject, iface, and core QGIS classes. Returns stdout/stderr.",
)
async def execute_code(ctx: Context, code: str) -> dict:
    if not await _confirm_destructive(
        ctx, "Execute arbitrary PyQGIS code? This can modify your project and system."
    ):
        return {"ok": False, "message": "Cancelled by user"}
    await ctx.info("Executing PyQGIS code...")
    await ctx.report_progress(0, 100)
    result = await _send("execute_code", {"code": code}, timeout=60)
    await ctx.report_progress(100, 100)
    return result


# --- Batch ---


@mcp.tool(
    title="Batch Commands",
    description="Execute multiple commands in a single round-trip. Each command is "
    '{"type": "<command_name>", "params": {...}}. Returns an array of results.',
)
async def batch_commands(ctx: Context, commands: list[dict]) -> dict:
    return await _send("batch", {"commands": commands}, timeout=60)


# --- Print Layouts ---


@mcp.tool(
    title="List Layouts",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="List all print layouts in the current project with names and page counts.",
)
async def list_layouts(ctx: Context) -> dict:
    return await _send("list_layouts")


@mcp.tool(
    title="Export Layout",
    annotations=ToolAnnotations(idempotentHint=True),
    description="Export a print layout to file. format: 'pdf', 'png', 'jpg', 'svg'. "
    "dpi: resolution (default 300).",
)
async def export_layout(
    ctx: Context,
    layout_name: str,
    path: str,
    format: str = "pdf",
    dpi: int = 300,
) -> dict:
    return await _send(
        "export_layout",
        {
            "layout_name": layout_name,
            "path": path,
            "format": format,
            "dpi": dpi,
        },
    )


# --- Message Log & Debugging ---


@mcp.tool(
    title="Get Message Log",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Get QGIS message log entries. Filter by level ('info', 'warning', 'critical') "
    "and/or tag (e.g. 'QGIS MCP'). Returns newest first.",
)
async def get_message_log(
    ctx: Context, level: str | None = None, tag: str | None = None, limit: int = 100
) -> dict:
    params = {"limit": limit}
    if level:
        params["level"] = level
    if tag:
        params["tag"] = tag
    return await _send("get_message_log", params)


# --- Plugin Management ---


@mcp.tool(
    title="List Plugins",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="List installed QGIS plugins with name, enabled status, and version. "
    "Set enabled_only=true to list only active plugins.",
)
async def list_plugins(ctx: Context, enabled_only: bool = False) -> dict:
    return await _send("list_plugins", {"enabled_only": enabled_only})


@mcp.tool(
    title="Get Plugin Info",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Get detailed info for a specific plugin: name, enabled, version, description, author, path.",
)
async def get_plugin_info(ctx: Context, plugin_name: str) -> dict:
    return await _send("get_plugin_info", {"plugin_name": plugin_name})


@mcp.tool(
    title="Reload Plugin",
    annotations=ToolAnnotations(destructiveHint=True),
    description="Reload a QGIS plugin by name. Cannot reload the MCP plugin itself. "
    "Useful during plugin development.",
)
async def reload_plugin(ctx: Context, plugin_name: str) -> dict:
    await ctx.info(f"Reloading plugin: {plugin_name}")
    return await _send("reload_plugin", {"plugin_name": plugin_name})


# --- Layer Tree ---


@mcp.tool(
    title="Get Layer Tree",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Get the full layer tree structure with groups and layers. "
    "Returns recursive tree with type, name, visibility, and children.",
)
async def get_layer_tree(ctx: Context) -> dict:
    return await _send("get_layer_tree")


@mcp.tool(
    title="Create Layer Group",
    description="Create a new layer group in the layer tree. "
    "Optionally specify a parent group name.",
)
async def create_layer_group(ctx: Context, name: str, parent: str | None = None) -> dict:
    params = {"name": name}
    if parent:
        params["parent"] = parent
    return await _send("create_layer_group", params)


@mcp.tool(title="Move Layer to Group", description="Move a layer into a layer group by group name.")
async def move_layer_to_group(ctx: Context, layer_id: str, group_name: str) -> dict:
    return await _send("move_layer_to_group", {"layer_id": layer_id, "group_name": group_name})


# --- Layer Properties ---


@mcp.tool(
    title="Set Layer Property",
    annotations=ToolAnnotations(idempotentHint=True),
    description="Set a layer property. Supported properties: opacity (0.0-1.0), name (string), "
    "min_scale, max_scale (float), scale_visibility (bool).",
)
async def set_layer_property(ctx: Context, layer_id: str, property: str, value: str) -> dict:
    return await _send(
        "set_layer_property", {"layer_id": layer_id, "property": property, "value": value}
    )


@mcp.tool(
    title="Get Layer Extent",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Get the spatial extent (bounding box) and CRS of a layer.",
)
async def get_layer_extent(ctx: Context, layer_id: str) -> dict:
    return await _send("get_layer_extent", {"layer_id": layer_id})


# --- Project Variables ---


@mcp.tool(
    title="Get Project Variables",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Get all project-level variables (key-value pairs set in Project Properties).",
)
async def get_project_variables(ctx: Context) -> dict:
    return await _send("get_project_variables")


@mcp.tool(
    title="Set Project Variable",
    annotations=ToolAnnotations(idempotentHint=True),
    description="Set a project-level variable. Variables are accessible in expressions as @key.",
)
async def set_project_variable(ctx: Context, key: str, value: str) -> dict:
    return await _send("set_project_variable", {"key": key, "value": value})


# --- Expression Validation ---


@mcp.tool(
    title="Validate Expression",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Validate a QGIS expression. Returns whether it's valid, any parse errors, "
    "and referenced column names. Optionally test against a layer's fields.",
)
async def validate_expression(ctx: Context, expression: str, layer_id: str | None = None) -> dict:
    params = {"expression": expression}
    if layer_id:
        params["layer_id"] = layer_id
    return await _send("validate_expression", params)


# --- Settings ---


@mcp.tool(
    title="Get Setting",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Read a QGIS setting by key path (e.g. 'qgis/sketching/sketching_enabled').",
)
async def get_setting(ctx: Context, key: str) -> dict:
    return await _send("get_setting", {"key": key})


@mcp.tool(
    title="Set Setting",
    annotations=ToolAnnotations(destructiveHint=True),
    description="Write a QGIS setting. Use with care — incorrect settings can affect QGIS behavior.",
)
async def set_setting(ctx: Context, key: str, value: str) -> dict:
    if not await _confirm_destructive(
        ctx, f"Set QGIS setting '{key}'? Incorrect settings can affect behavior."
    ):
        return {"ok": False, "message": "Cancelled by user"}
    return await _send("set_setting", {"key": key, "value": value})


# --- CRS Transformation ---


@mcp.tool(
    title="Transform Coordinates",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Transform coordinates between CRS. Accepts a single point {x, y}, "
    "a list of points [{x, y}, ...], or a bbox {xmin, ymin, xmax, ymax}. "
    "Returns transformed coordinates in the same format.",
)
async def transform_coordinates(
    ctx: Context,
    source_crs: str,
    target_crs: str,
    point: dict | None = None,
    points: list[dict] | None = None,
    bbox: dict | None = None,
) -> dict:
    params = {"source_crs": source_crs, "target_crs": target_crs}
    if point:
        params["point"] = point
    if points:
        params["points"] = points
    if bbox:
        params["bbox"] = bbox
    return await _send("transform_coordinates", params)


# ===========================================================================
# MCP COMPLETIONS
# ===========================================================================

_completion_cache: list[str] = []
_completion_cache_at: float = 0.0
_COMPLETION_TTL: float = 10.0  # seconds — avoids hitting QGIS on every keystroke


@mcp.completion()
async def handle_completion(ref, argument: CompletionArgument, context=None):
    """Auto-complete layer_id arguments with available layer IDs.

    Uses a TTL cache to avoid querying QGIS on every keystroke.
    """
    global _completion_cache, _completion_cache_at

    if argument.name == "layer_id":
        try:
            now = time.monotonic()
            if now - _completion_cache_at >= _COMPLETION_TTL or not _completion_cache:
                result = await _send("get_layers", {"limit": 200, "offset": 0})
                layers = result.get("layers", [])
                _completion_cache = [layer["id"] for layer in layers]
                _completion_cache_at = now
            ids = _completion_cache
            if argument.value:
                prefix = argument.value.lower()
                ids = [lid for lid in ids if prefix in lid.lower()]
            return Completion(values=ids[:50])
        except Exception:
            return None
    return None


# ===========================================================================
# MCP RESOURCES
# ===========================================================================


@mcp.resource(
    "qgis://info", name="qgis_info", description="QGIS version, profile, and plugin count"
)
def qgis_info_resource() -> str:
    return json.dumps(_send_sync("get_qgis_info"))


@mcp.resource(
    "qgis://project",
    name="project_info",
    description="Current project metadata, CRS, layer count, layer summary",
)
def project_info_resource() -> str:
    return json.dumps(_send_sync("get_project_info"))


@mcp.resource(
    "qgis://layers", name="layer_list", description="All layers with IDs, names, types, visibility"
)
def layers_resource() -> str:
    return json.dumps(_send_sync("get_layers"))


@mcp.resource(
    "qgis://layers/{layer_id}/info",
    name="layer_info",
    description="Detailed layer info: CRS, extent, fields, feature count, source, provider",
)
def layer_info_resource(layer_id: str) -> str:
    return json.dumps(_send_sync("get_layer_info", {"layer_id": layer_id}))


@mcp.resource(
    "qgis://layers/{layer_id}/features",
    name="layer_features",
    description="Sample features (first 10) from a vector layer",
)
def layer_features_resource(layer_id: str) -> str:
    return json.dumps(_send_sync("get_layer_features", {"layer_id": layer_id, "limit": 10}))


@mcp.resource(
    "qgis://layers/{layer_id}/schema",
    name="layer_schema",
    description="Field names, types, and lengths for a vector layer",
)
def layer_schema_resource(layer_id: str) -> str:
    return json.dumps(_send_sync("get_layer_schema", {"layer_id": layer_id}))


@mcp.resource(
    "qgis://llms.txt",
    name="llms_context",
    description="Capabilities summary for LLM context — lists all tools, resources, and usage tips",
)
def llms_context_resource() -> str:
    return """# QGIS MCP — LLM Context

## Overview
QGIS MCP connects QGIS Desktop to LLMs via the Model Context Protocol.
50 tools for project management, layer operations, feature editing, styling, processing, and more.

## Quick Start
1. `ping` — verify connectivity
2. `get_project_info` — understand current project
3. `get_layers` — list available layers
4. `get_layer_features` — inspect data (expression filtering, pagination)
5. `render_map` or `get_canvas_screenshot` — see the map

## Tool Categories
- **Info**: ping, get_qgis_info, get_project_info
- **Project**: load_project, create_new_project, save_project
- **Layers**: get_layers, add_vector_layer, add_raster_layer, remove_layer, find_layer, create_memory_layer
- **Visibility**: set_layer_visibility, zoom_to_layer
- **Features**: get_layer_features (max 50, filter with expressions), get_field_statistics
- **Editing**: add_features, update_features, delete_features
- **Selection**: select_features, get_selection, clear_selection
- **Styling**: set_layer_style (single/categorized/graduated)
- **Canvas**: get_canvas_extent, set_canvas_extent, get_canvas_screenshot
- **Raster**: get_raster_info
- **Processing**: execute_processing, list_processing_algorithms, get_algorithm_help
- **Rendering**: render_map (re-render to image), get_canvas_screenshot (fast grab)
- **Code**: execute_code (arbitrary PyQGIS)
- **Batch**: batch_commands (multiple commands in one round-trip)
- **Layouts**: list_layouts, export_layout
- **Logging**: get_message_log
- **Plugins**: list_plugins, get_plugin_info, reload_plugin
- **Layer Tree**: get_layer_tree, create_layer_group, move_layer_to_group
- **Properties**: set_layer_property, get_layer_extent
- **Variables**: get_project_variables, set_project_variable
- **Expression**: validate_expression
- **Settings**: get_setting, set_setting
- **CRS**: transform_coordinates

## Key Patterns
- Layer IDs are used to reference layers (get them from get_layers or find_layer)
- Features are flat dicts: {"_fid": 1, "name": "Berlin", "_geometry": "POINT(...)"}
- Use expressions for server-side filtering: "population > 1000000"
- Processing algorithms: search with list_processing_algorithms, get params with get_algorithm_help
- render_map returns inline images; get_canvas_screenshot is faster (no re-render)
- Destructive operations (remove_layer, delete_features, set_setting) may ask for confirmation

## Resources (read-only data)
- qgis://info — QGIS version info
- qgis://project — project metadata
- qgis://layers — all layers
- qgis://layers/{id}/info — layer details
- qgis://layers/{id}/features — sample features
- qgis://layers/{id}/schema — field schema
- qgis://llms.txt — this context file

## Environment Variables
- QGIS_MCP_HOST — server host (default: localhost)
- QGIS_MCP_PORT — server port (default: 9876)
- QGIS_MCP_TRANSPORT — "stdio" (default) or "streamable-http"
"""


# ===========================================================================
# MCP PROMPTS
# ===========================================================================


@mcp.prompt(
    name="analyze_layer",
    description="Inspect a layer's schema, sample data, and compute field statistics",
)
def analyze_layer_prompt(layer_id: str) -> list[UserMessage]:
    return [
        UserMessage(
            content=f"Analyze the layer with ID '{layer_id}':\n"
            f"1. Read resource qgis://layers/{layer_id}/schema to understand fields\n"
            f"2. Read resource qgis://layers/{layer_id}/features to see sample data\n"
            f"3. For each numeric field, call get_field_statistics to compute stats\n"
            f"4. Summarize the layer: geometry type, field types, data distribution, any issues"
        )
    ]


@mcp.prompt(
    name="spatial_analysis",
    description="Run a spatial operation between two layers with CRS validation",
)
def spatial_analysis_prompt(
    input_layer: str, overlay_layer: str, operation: str
) -> list[UserMessage]:
    return [
        UserMessage(
            content=f"Perform a spatial {operation} between layers:\n"
            f"- Input: {input_layer}\n"
            f"- Overlay: {overlay_layer}\n"
            f"Steps:\n"
            f"1. Get info for both layers (get_layers or qgis://layers/ID/info)\n"
            f"2. Verify both layers are vector layers with compatible geometry types\n"
            f"3. Check that CRS matches; if not, reproject one layer first\n"
            f"4. Use execute_processing with the appropriate algorithm (e.g. native:intersection, native:union)\n"
            f"5. Report the result layer's feature count and fields"
        )
    ]


@mcp.prompt(
    name="style_map", description="Create a thematic map with categorized or graduated symbology"
)
def style_map_prompt(layer_id: str, field: str) -> list[UserMessage]:
    return [
        UserMessage(
            content=f"Style layer '{layer_id}' based on field '{field}':\n"
            f"1. Get the layer schema and sample data to understand the field values\n"
            f"2. Call get_field_statistics for '{field}' to understand the data distribution\n"
            f"3. If the field is categorical, use set_layer_style with style_type='categorized'\n"
            f"4. If the field is numeric, use set_layer_style with style_type='graduated'\n"
            f"5. Refresh the canvas and render a preview image with render_map"
        )
    ]


# ===========================================================================
# Entry point
# ===========================================================================

def main():
    transport = os.environ.get("QGIS_MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
