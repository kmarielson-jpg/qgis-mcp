"""Unit tests for MCP server tools with mocked socket connection."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qgis_mcp.server import QgisMCPClient, _send_sync

# --- Fixtures ---


@pytest.fixture
def mock_connection():
    """Provide a mocked QgisMCPClient that returns configurable responses."""
    mock_client = MagicMock(spec=QgisMCPClient)
    mock_client.socket = MagicMock()
    mock_client.socket.getpeername.return_value = ("localhost", 9876)
    with patch("qgis_mcp.server.get_qgis_connection", return_value=mock_client):
        yield mock_client


def _make_ctx():
    """Create a mock Context with async methods."""
    ctx = MagicMock()
    ctx.report_progress = AsyncMock()
    ctx.info = AsyncMock()
    ctx.warning = AsyncMock()
    ctx.error = AsyncMock()
    ctx.elicit = AsyncMock(side_effect=Exception("Elicitation not supported"))
    return ctx


# --- _send_sync() helper tests ---


def test_send_unwraps_success_envelope(mock_connection):
    mock_connection.send_command.return_value = {"status": "success", "result": {"pong": True}}
    result = _send_sync("ping")
    assert result == {"pong": True}
    mock_connection.send_command.assert_called_once_with("ping", None, timeout=30)


def test_send_raises_on_error(mock_connection):
    mock_connection.send_command.return_value = {"status": "error", "message": "Layer not found"}
    with pytest.raises(RuntimeError, match="Layer not found"):
        _send_sync("get_layer_features", {"layer_id": "bad_id"})


def test_send_raises_on_none_response(mock_connection):
    mock_connection.send_command.return_value = None
    with pytest.raises(RuntimeError, match="No response"):
        _send_sync("ping")


def test_send_passes_timeout(mock_connection):
    mock_connection.send_command.return_value = {"status": "success", "result": {}}
    _send_sync("execute_processing", {"algorithm": "test"}, timeout=60)
    mock_connection.send_command.assert_called_once_with(
        "execute_processing", {"algorithm": "test"}, timeout=60
    )


def test_send_empty_result(mock_connection):
    mock_connection.send_command.return_value = {"status": "success"}
    result = _send_sync("ping")
    assert result == {}


# --- Tool-level tests (all async) ---


@pytest.mark.asyncio
async def test_ping_tool_returns_dict(mock_connection):
    mock_connection.send_command.return_value = {"status": "success", "result": {"pong": True}}
    from qgis_mcp.server import ping

    ctx = _make_ctx()
    output = await ping(ctx)
    assert isinstance(output, dict)
    assert output == {"pong": True}


@pytest.mark.asyncio
async def test_get_layers_passes_pagination(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"layers": [], "total_count": 0, "offset": 5, "limit": 10},
    }
    from qgis_mcp.server import get_layers

    ctx = _make_ctx()
    output = await get_layers(ctx, limit=10, offset=5)
    assert isinstance(output, dict)
    assert output["total_count"] == 0
    mock_connection.send_command.assert_called_once_with(
        "get_layers", {"limit": 10, "offset": 5}, timeout=30
    )


@pytest.mark.asyncio
async def test_get_layer_features_enforces_max_limit(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"features": [], "feature_count": 0, "fields": []},
    }
    from qgis_mcp.server import get_layer_features

    ctx = _make_ctx()
    await get_layer_features(ctx, layer_id="test", limit=100)
    # Should have been capped to 50
    call_params = mock_connection.send_command.call_args[0][1]
    assert call_params["limit"] == 50


@pytest.mark.asyncio
async def test_get_layer_features_with_expression(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"features": [], "feature_count": 0, "fields": []},
    }
    from qgis_mcp.server import get_layer_features

    ctx = _make_ctx()
    await get_layer_features(ctx, layer_id="test", expression="name = 'Berlin'")
    call_params = mock_connection.send_command.call_args[0][1]
    assert call_params["expression"] == "name = 'Berlin'"


@pytest.mark.asyncio
async def test_get_layer_features_no_expression_omitted(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"features": [], "feature_count": 0, "fields": []},
    }
    from qgis_mcp.server import get_layer_features

    ctx = _make_ctx()
    await get_layer_features(ctx, layer_id="test")
    call_params = mock_connection.send_command.call_args[0][1]
    assert "expression" not in call_params


@pytest.mark.asyncio
async def test_batch_commands_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": [
            {"status": "success", "result": {"pong": True}},
            {"status": "success", "result": {"layers": [], "total_count": 0}},
        ],
    }
    from qgis_mcp.server import batch_commands

    ctx = _make_ctx()
    output = await batch_commands(
        ctx,
        commands=[
            {"type": "ping", "params": {}},
            {"type": "get_layers", "params": {}},
        ],
    )
    assert isinstance(output, dict | list)
    assert len(output) == 2


@pytest.mark.asyncio
async def test_execute_processing_uses_long_timeout(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"algorithm": "test", "result": {}},
    }
    from qgis_mcp.server import execute_processing

    ctx = _make_ctx()
    await execute_processing(ctx, algorithm="native:buffer", parameters={"INPUT": "layer"})
    mock_connection.send_command.assert_called_once_with(
        "execute_processing",
        {"algorithm": "native:buffer", "parameters": {"INPUT": "layer"}},
        timeout=60,
    )
    ctx.info.assert_awaited_once_with("Running algorithm: native:buffer")


@pytest.mark.asyncio
async def test_render_map_returns_image_content(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"base64_data": "iVBOR==", "mime_type": "image/png", "width": 800, "height": 600},
    }
    from qgis_mcp.server import render_map

    ctx = _make_ctx()
    result = await render_map(ctx, width=800, height=600)
    assert isinstance(result, list)
    assert result[0].type == "image"
    assert result[0].data == "iVBOR=="
    ctx.info.assert_awaited_once_with("Rendering map...")


@pytest.mark.asyncio
async def test_execute_code_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"stdout": "hello", "stderr": ""},
    }
    from qgis_mcp.server import execute_code

    ctx = _make_ctx()
    result = await execute_code(ctx, code="print('hello')")
    assert result["stdout"] == "hello"
    ctx.info.assert_awaited_once_with("Executing PyQGIS code...")


# --- QgisMCPClient tests ---


def test_client_send_command_no_socket():
    client = QgisMCPClient()
    with pytest.raises(ConnectionError):
        client.send_command("ping")


# --- Phase 2 new tool tests ---


@pytest.mark.asyncio
async def test_add_features_tool(mock_connection):
    mock_connection.send_command.return_value = {"status": "success", "result": {"added": 2}}
    from qgis_mcp.server import add_features

    ctx = _make_ctx()
    output = await add_features(
        ctx,
        layer_id="test",
        features=[
            {"attributes": {"name": "A"}, "geometry_wkt": "POINT(0 0)"},
            {"attributes": {"name": "B"}, "geometry_wkt": "POINT(1 1)"},
        ],
    )
    assert output == {"added": 2}


@pytest.mark.asyncio
async def test_update_features_tool(mock_connection):
    mock_connection.send_command.return_value = {"status": "success", "result": {"updated": 1}}
    from qgis_mcp.server import update_features

    ctx = _make_ctx()
    output = await update_features(
        ctx,
        layer_id="test",
        updates=[
            {"fid": 1, "attributes": {"name": "Updated"}},
        ],
    )
    assert output == {"updated": 1}


@pytest.mark.asyncio
async def test_delete_features_by_fids(mock_connection):
    mock_connection.send_command.return_value = {"status": "success", "result": {"deleted": 2}}
    from qgis_mcp.server import delete_features

    ctx = _make_ctx()
    output = await delete_features(ctx, layer_id="test", fids=[1, 2])
    assert output == {"deleted": 2}
    call_params = mock_connection.send_command.call_args[0][1]
    assert call_params["fids"] == [1, 2]


@pytest.mark.asyncio
async def test_delete_features_by_expression(mock_connection):
    mock_connection.send_command.return_value = {"status": "success", "result": {"deleted": 3}}
    from qgis_mcp.server import delete_features

    ctx = _make_ctx()
    await delete_features(ctx, layer_id="test", expression="id > 5")
    call_params = mock_connection.send_command.call_args[0][1]
    assert call_params["expression"] == "id > 5"


@pytest.mark.asyncio
async def test_set_layer_style_tool(mock_connection):
    mock_connection.send_command.return_value = {"status": "success", "result": {"ok": True}}
    from qgis_mcp.server import set_layer_style

    ctx = _make_ctx()
    output = await set_layer_style(
        ctx,
        layer_id="test",
        style_type="categorized",
        field="name",
        classes=5,
        color_ramp="Spectral",
    )
    assert output == {"ok": True}
    call_params = mock_connection.send_command.call_args[0][1]
    assert call_params["style_type"] == "categorized"
    assert call_params["field"] == "name"


@pytest.mark.asyncio
async def test_select_features_tool(mock_connection):
    mock_connection.send_command.return_value = {"status": "success", "result": {"selected": 3}}
    from qgis_mcp.server import select_features

    ctx = _make_ctx()
    output = await select_features(ctx, layer_id="test", expression="value > 100")
    assert output == {"selected": 3}


@pytest.mark.asyncio
async def test_get_selection_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"fids": [1, 2, 3], "count": 3},
    }
    from qgis_mcp.server import get_selection

    ctx = _make_ctx()
    output = await get_selection(ctx, layer_id="test")
    assert output["count"] == 3
    assert output["fids"] == [1, 2, 3]


@pytest.mark.asyncio
async def test_clear_selection_tool(mock_connection):
    mock_connection.send_command.return_value = {"status": "success", "result": {"ok": True}}
    from qgis_mcp.server import clear_selection

    ctx = _make_ctx()
    output = await clear_selection(ctx, layer_id="test")
    assert output == {"ok": True}


@pytest.mark.asyncio
async def test_create_memory_layer_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"id": "mem_123", "name": "test_layer", "type": "vector_0", "feature_count": 0},
    }
    from qgis_mcp.server import create_memory_layer

    ctx = _make_ctx()
    output = await create_memory_layer(
        ctx, name="test_layer", geometry_type="Point", fields=[{"name": "id", "type": "integer"}]
    )
    assert output["id"] == "mem_123"
    call_params = mock_connection.send_command.call_args[0][1]
    assert call_params["geometry_type"] == "Point"
    assert call_params["fields"] == [{"name": "id", "type": "integer"}]


@pytest.mark.asyncio
async def test_list_processing_algorithms_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {
            "algorithms": [{"id": "native:buffer", "name": "Buffer", "provider": "native"}],
            "count": 1,
        },
    }
    from qgis_mcp.server import list_processing_algorithms

    ctx = _make_ctx()
    output = await list_processing_algorithms(ctx, search="buffer")
    assert output["count"] == 1
    assert output["algorithms"][0]["id"] == "native:buffer"


@pytest.mark.asyncio
async def test_get_algorithm_help_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {
            "id": "native:buffer",
            "name": "Buffer",
            "parameters": [],
            "outputs": [],
            "description": "",
            "provider": "native",
        },
    }
    from qgis_mcp.server import get_algorithm_help

    ctx = _make_ctx()
    output = await get_algorithm_help(ctx, algorithm_id="native:buffer")
    assert output["id"] == "native:buffer"


@pytest.mark.asyncio
async def test_find_layer_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"layers": [{"id": "l1", "name": "roads", "type": "vector_1"}], "count": 1},
    }
    from qgis_mcp.server import find_layer

    ctx = _make_ctx()
    output = await find_layer(ctx, name_pattern="road*")
    assert output["count"] == 1


@pytest.mark.asyncio
async def test_list_layouts_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"layouts": [{"name": "Map1", "page_count": 1}], "count": 1},
    }
    from qgis_mcp.server import list_layouts

    ctx = _make_ctx()
    output = await list_layouts(ctx)
    assert output["count"] == 1


@pytest.mark.asyncio
async def test_export_layout_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"ok": True, "path": "/tmp/layout.pdf"},
    }
    from qgis_mcp.server import export_layout

    ctx = _make_ctx()
    output = await export_layout(ctx, layout_name="Map1", path="/tmp/layout.pdf")
    assert output["ok"] is True
    call_params = mock_connection.send_command.call_args[0][1]
    assert call_params["format"] == "pdf"
    assert call_params["dpi"] == 300


# --- Phase 3 new tool tests ---


@pytest.mark.asyncio
async def test_get_message_log_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {
            "messages": [
                {
                    "tag": "QGIS MCP",
                    "message": "test",
                    "level": "info",
                    "timestamp": "2026-03-07T12:00:00",
                }
            ],
            "count": 1,
        },
    }
    from qgis_mcp.server import get_message_log

    ctx = _make_ctx()
    output = await get_message_log(ctx, limit=50)
    assert output["count"] == 1
    mock_connection.send_command.assert_called_once_with(
        "get_message_log", {"limit": 50}, timeout=30
    )


@pytest.mark.asyncio
async def test_get_message_log_with_filters(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"messages": [], "count": 0},
    }
    from qgis_mcp.server import get_message_log

    ctx = _make_ctx()
    await get_message_log(ctx, level="warning", tag="MyPlugin", limit=10)
    call_params = mock_connection.send_command.call_args[0][1]
    assert call_params["level"] == "warning"
    assert call_params["tag"] == "MyPlugin"
    assert call_params["limit"] == 10


@pytest.mark.asyncio
async def test_list_plugins_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {
            "plugins": [
                {
                    "name": "qgis_mcp_plugin",
                    "enabled": True,
                    "version": "0.3.0",
                    "path": "/plugins/qgis_mcp_plugin",
                }
            ],
            "count": 1,
        },
    }
    from qgis_mcp.server import list_plugins

    ctx = _make_ctx()
    output = await list_plugins(ctx, enabled_only=True)
    assert output["count"] == 1
    assert output["plugins"][0]["name"] == "qgis_mcp_plugin"


@pytest.mark.asyncio
async def test_get_plugin_info_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {
            "name": "qgis_mcp_plugin",
            "enabled": True,
            "version": "0.3.0",
            "description": "MCP Plugin",
            "author": "Test",
            "path": "/plugins/qgis_mcp_plugin",
        },
    }
    from qgis_mcp.server import get_plugin_info

    ctx = _make_ctx()
    output = await get_plugin_info(ctx, plugin_name="qgis_mcp_plugin")
    assert output["name"] == "qgis_mcp_plugin"
    assert output["enabled"] is True


@pytest.mark.asyncio
async def test_reload_plugin_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"reloaded": "my_plugin", "ok": True},
    }
    from qgis_mcp.server import reload_plugin

    ctx = _make_ctx()
    output = await reload_plugin(ctx, plugin_name="my_plugin")
    assert output["ok"] is True
    assert output["reloaded"] == "my_plugin"
    ctx.info.assert_awaited_once_with("Reloading plugin: my_plugin")


@pytest.mark.asyncio
async def test_reload_plugin_self_blocked(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "error",
        "message": "Cannot reload MCP plugin (would break the connection)",
    }
    from qgis_mcp.server import reload_plugin

    ctx = _make_ctx()
    with pytest.raises(RuntimeError, match="Cannot reload MCP plugin"):
        await reload_plugin(ctx, plugin_name="qgis_mcp_plugin")


@pytest.mark.asyncio
async def test_get_layer_tree_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {
            "children": [
                {
                    "type": "group",
                    "name": "Base Maps",
                    "visible": True,
                    "children": [
                        {
                            "type": "layer",
                            "name": "OSM",
                            "visible": True,
                            "layer_id": "osm_123",
                            "layer_type": "raster",
                        }
                    ],
                },
                {
                    "type": "layer",
                    "name": "Roads",
                    "visible": True,
                    "layer_id": "roads_456",
                    "layer_type": "vector_1",
                },
            ]
        },
    }
    from qgis_mcp.server import get_layer_tree

    ctx = _make_ctx()
    output = await get_layer_tree(ctx)
    assert len(output["children"]) == 2
    assert output["children"][0]["type"] == "group"
    assert output["children"][0]["children"][0]["type"] == "layer"


@pytest.mark.asyncio
async def test_create_layer_group_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"name": "My Group", "ok": True},
    }
    from qgis_mcp.server import create_layer_group

    ctx = _make_ctx()
    output = await create_layer_group(ctx, name="My Group")
    assert output["ok"] is True
    call_params = mock_connection.send_command.call_args[0][1]
    assert call_params["name"] == "My Group"
    assert "parent" not in call_params


@pytest.mark.asyncio
async def test_create_layer_group_with_parent(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"name": "Sub Group", "ok": True},
    }
    from qgis_mcp.server import create_layer_group

    ctx = _make_ctx()
    await create_layer_group(ctx, name="Sub Group", parent="Parent Group")
    call_params = mock_connection.send_command.call_args[0][1]
    assert call_params["parent"] == "Parent Group"


@pytest.mark.asyncio
async def test_move_layer_to_group_tool(mock_connection):
    mock_connection.send_command.return_value = {"status": "success", "result": {"ok": True}}
    from qgis_mcp.server import move_layer_to_group

    ctx = _make_ctx()
    output = await move_layer_to_group(ctx, layer_id="layer_123", group_name="My Group")
    assert output["ok"] is True


@pytest.mark.asyncio
async def test_set_layer_property_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"ok": True, "property": "opacity", "value": "0.5"},
    }
    from qgis_mcp.server import set_layer_property

    ctx = _make_ctx()
    output = await set_layer_property(ctx, layer_id="test", property="opacity", value="0.5")
    assert output["ok"] is True
    assert output["property"] == "opacity"


@pytest.mark.asyncio
async def test_get_layer_extent_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"xmin": 0.0, "ymin": 0.0, "xmax": 10.0, "ymax": 10.0, "crs": "EPSG:4326"},
    }
    from qgis_mcp.server import get_layer_extent

    ctx = _make_ctx()
    output = await get_layer_extent(ctx, layer_id="test")
    assert output["crs"] == "EPSG:4326"
    assert output["xmax"] == 10.0


@pytest.mark.asyncio
async def test_get_project_variables_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"variables": {"project_title": "Test", "custom_var": "42"}},
    }
    from qgis_mcp.server import get_project_variables

    ctx = _make_ctx()
    output = await get_project_variables(ctx)
    assert "project_title" in output["variables"]


@pytest.mark.asyncio
async def test_set_project_variable_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"ok": True, "key": "my_var", "value": "hello"},
    }
    from qgis_mcp.server import set_project_variable

    ctx = _make_ctx()
    output = await set_project_variable(ctx, key="my_var", value="hello")
    assert output["ok"] is True
    assert output["key"] == "my_var"


@pytest.mark.asyncio
async def test_validate_expression_valid(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"valid": True, "referenced_columns": []},
    }
    from qgis_mcp.server import validate_expression

    ctx = _make_ctx()
    output = await validate_expression(ctx, expression="1 + 1")
    assert output["valid"] is True


@pytest.mark.asyncio
async def test_validate_expression_with_layer(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"valid": True, "referenced_columns": ["name"]},
    }
    from qgis_mcp.server import validate_expression

    ctx = _make_ctx()
    await validate_expression(ctx, expression="\"name\" = 'Berlin'", layer_id="test_layer")
    call_params = mock_connection.send_command.call_args[0][1]
    assert call_params["layer_id"] == "test_layer"
    assert call_params["expression"] == "\"name\" = 'Berlin'"


@pytest.mark.asyncio
async def test_validate_expression_without_layer(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"valid": True, "referenced_columns": []},
    }
    from qgis_mcp.server import validate_expression

    ctx = _make_ctx()
    await validate_expression(ctx, expression="1 + 1")
    call_params = mock_connection.send_command.call_args[0][1]
    assert "layer_id" not in call_params


@pytest.mark.asyncio
async def test_get_setting_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"key": "qgis/sketching/sketching_enabled", "value": True, "exists": True},
    }
    from qgis_mcp.server import get_setting

    ctx = _make_ctx()
    output = await get_setting(ctx, key="qgis/sketching/sketching_enabled")
    assert output["exists"] is True


@pytest.mark.asyncio
async def test_set_setting_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {"ok": True, "key": "qgis/sketching/sketching_enabled"},
    }
    from qgis_mcp.server import set_setting

    ctx = _make_ctx()
    output = await set_setting(ctx, key="qgis/sketching/sketching_enabled", value="true")
    assert output["ok"] is True


# --- Phase 4 new tool tests ---


@pytest.mark.asyncio
async def test_get_canvas_screenshot_tool(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {
            "base64_data": "iVBOR==",
            "mime_type": "image/png",
            "width": 1024,
            "height": 768,
        },
    }
    from qgis_mcp.server import get_canvas_screenshot

    ctx = _make_ctx()
    result = await get_canvas_screenshot(ctx)
    assert isinstance(result, list)
    assert result[0].type == "image"
    assert result[0].data == "iVBOR=="


@pytest.mark.asyncio
async def test_transform_coordinates_point(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {
            "source_crs": "EPSG:4326",
            "target_crs": "EPSG:3857",
            "point": {"x": 1113194.91, "y": 0.0},
        },
    }
    from qgis_mcp.server import transform_coordinates

    ctx = _make_ctx()
    output = await transform_coordinates(
        ctx, source_crs="EPSG:4326", target_crs="EPSG:3857", point={"x": 10.0, "y": 0.0}
    )
    assert output["point"]["x"] == 1113194.91
    call_params = mock_connection.send_command.call_args[0][1]
    assert call_params["source_crs"] == "EPSG:4326"
    assert call_params["target_crs"] == "EPSG:3857"
    assert call_params["point"] == {"x": 10.0, "y": 0.0}


@pytest.mark.asyncio
async def test_transform_coordinates_bbox(mock_connection):
    mock_connection.send_command.return_value = {
        "status": "success",
        "result": {
            "source_crs": "EPSG:4326",
            "target_crs": "EPSG:3857",
            "bbox": {"xmin": 0.0, "ymin": 0.0, "xmax": 1113194.91, "ymax": 1118889.97},
        },
    }
    from qgis_mcp.server import transform_coordinates

    ctx = _make_ctx()
    output = await transform_coordinates(
        ctx,
        source_crs="EPSG:4326",
        target_crs="EPSG:3857",
        bbox={"xmin": 0, "ymin": 0, "xmax": 10, "ymax": 10},
    )
    assert "bbox" in output
    call_params = mock_connection.send_command.call_args[0][1]
    assert call_params["bbox"] == {"xmin": 0, "ymin": 0, "xmax": 10, "ymax": 10}


# --- Elicitation tests ---


@pytest.mark.asyncio
async def test_remove_layer_proceeds_without_elicitation(mock_connection):
    """When elicitation not supported (raises), tool proceeds anyway."""
    mock_connection.send_command.return_value = {"status": "success", "result": {"ok": True}}
    from qgis_mcp.server import remove_layer

    ctx = _make_ctx()
    output = await remove_layer(ctx, layer_id="test_layer")
    assert output == {"ok": True}


@pytest.mark.asyncio
async def test_remove_layer_cancelled_by_user(mock_connection):
    """When user declines elicitation, tool returns cancelled."""
    from qgis_mcp.server import remove_layer

    ctx = _make_ctx()
    elicit_response = MagicMock()
    elicit_response.action = "accept"
    elicit_response.data = {"confirm": False}
    ctx.elicit = AsyncMock(return_value=elicit_response)
    output = await remove_layer(ctx, layer_id="test_layer")
    assert output == {"ok": False, "message": "Cancelled by user"}
    mock_connection.send_command.assert_not_called()


@pytest.mark.asyncio
async def test_remove_layer_confirmed_by_user(mock_connection):
    """When user confirms elicitation, tool proceeds."""
    mock_connection.send_command.return_value = {"status": "success", "result": {"ok": True}}
    from qgis_mcp.server import remove_layer

    ctx = _make_ctx()
    elicit_response = MagicMock()
    elicit_response.action = "accept"
    elicit_response.data = {"confirm": True}
    ctx.elicit = AsyncMock(return_value=elicit_response)
    output = await remove_layer(ctx, layer_id="test_layer")
    assert output == {"ok": True}


# --- Env var configuration tests ---


def test_env_var_host_port():
    """Test that get_qgis_connection uses QGIS_MCP_HOST/PORT env vars."""
    with (
        patch.dict(os.environ, {"QGIS_MCP_HOST": "192.168.1.100", "QGIS_MCP_PORT": "9999"}),
        patch("qgis_mcp.server.QgisMCPClient") as mock_client_cls,
    ):
        mock_instance = MagicMock()
        mock_instance.connect.return_value = True
        mock_instance.socket = MagicMock()
        mock_client_cls.return_value = mock_instance

        import qgis_mcp.server as srv

        srv._qgis_connection = None
        try:
            srv.get_qgis_connection()
            mock_client_cls.assert_called_once_with(host="192.168.1.100", port=9999)
        finally:
            srv._qgis_connection = None


@pytest.mark.asyncio
async def test_load_project_logs_info(mock_connection):
    """Test that load_project sends ctx.info() message."""
    mock_connection.send_command.return_value = {"status": "success", "result": {"ok": True}}
    from qgis_mcp.server import load_project

    ctx = _make_ctx()
    await load_project(ctx, path="/tmp/test.qgz")
    ctx.info.assert_awaited_once_with("Loading project: /tmp/test.qgz")
