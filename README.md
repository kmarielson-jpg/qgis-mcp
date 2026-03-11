# QGIS MCP

Connect [QGIS](https://qgis.org/) to [Claude AI](https://claude.ai/) through the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/), enabling Claude to directly control QGIS — manage layers, edit features, run processing algorithms, render maps, and more.

50 MCP tools covering layer management, feature editing, processing, rendering, styling, plugin development, and system management. Compatible with QGIS 3.28–4.x.

Based on the [BlenderMCP](https://github.com/ahujasid/blender-mcp) project by [Siddharth Ahuja](https://x.com/sidahuj) and the original [qgis_mcp](https://github.com/jjsantos01/qgis_mcp) by [Juan Santos](https://x.com/jjsantoso).

## Architecture

```
Claude ←→ MCP Server (FastMCP) ←→ TCP socket ←→ QGIS Plugin (QTimer) ←→ PyQGIS API
```

1. **QGIS Plugin** (`qgis_mcp_plugin/`) — Runs inside QGIS. Non-blocking TCP socket server that processes JSON commands within QGIS's event loop.
2. **MCP Server** (`src/qgis_mcp/qgis_mcp_server.py`) — Runs outside QGIS. Exposes QGIS operations as MCP tools via [FastMCP](https://github.com/jlowin/fastmcp).

## Prerequisites

- **QGIS** 3.28 or newer
- **Python** 3.12+
- **uv** package manager — [install uv](https://docs.astral.sh/uv/getting-started/installation/)

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/nkarasiak/qgis-mcp.git
cd qgis-mcp
```

### 2. Install the QGIS plugin

Copy (or symlink) the `qgis_mcp_plugin/` folder into your QGIS plugins directory:

**Find your plugins folder:** In QGIS, go to `Settings` > `User Profiles` > `Open Active Profile Folder`, then navigate to `python/plugins/`.

| OS | Typical path |
|----|-------------|
| Linux | `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/` |
| macOS | `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/` |
| Windows | `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\` |

```bash
# Example on Linux (symlink recommended for development)
ln -s /path/to/qgis-mcp/qgis_mcp_plugin ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/qgis_mcp_plugin
```

Restart QGIS, then enable the plugin: `Plugins` > `Manage and Install Plugins` > search "QGIS MCP" > check the box.

### 3. Connect your MCP client

#### Claude Code (CLI) — one-liner

```bash
claude mcp add --transport stdio qgis-mcp -- uvx --from git+https://github.com/nkarasiak/qgis-mcp qgis-mcp-server
```

#### Claude Desktop — manual config

Go to `Claude` > `Settings` > `Developer` > `Edit Config` and add:

```json
{
  "mcpServers": {
    "qgis": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/nkarasiak/qgis-mcp",
        "qgis-mcp-server"
      ]
    }
  }
}
```

#### Cursor / other MCP clients

Use the same JSON configuration above in your client's MCP settings file.

#### From a local clone (for development)

```bash
claude mcp add --transport stdio qgis-mcp -- uv run --directory /path/to/qgis-mcp --no-sync src/qgis_mcp/qgis_mcp_server.py
```

## Usage

1. **Start the plugin** — In QGIS, click the MCP toolbar button (or `Plugins` > `QGIS MCP`) and click "Start Server"
2. **Talk to Claude** — The MCP tools will appear automatically. Ask Claude to work with your QGIS project.

### Example prompt

```
You have access to QGIS tools. Do the following:
1. Ping to check the connection
2. Create a new project and save it at "/tmp/my_project.qgz"
3. Load the vector layer "/data/cities.shp" and name it "Cities"
4. Get field statistics for the "population" field
5. Create a graduated symbology on the "population" field with 5 classes
6. Render the map and show me the result
7. Save the project
```

## Tools (50)

| Category | Tools |
|----------|-------|
| **Project** | `load_project`, `create_new_project`, `save_project`, `get_project_info` |
| **Layers** | `get_layers`, `add_vector_layer`, `add_raster_layer`, `remove_layer`, `find_layer`, `create_memory_layer`, `set_layer_visibility`, `zoom_to_layer`, `get_layer_extent`, `set_layer_property` |
| **Features** | `get_layer_features`, `add_features`, `update_features`, `delete_features`, `select_features`, `get_selection`, `clear_selection`, `get_field_statistics` |
| **Styling** | `set_layer_style` (single, categorized, graduated) |
| **Rendering** | `render_map`, `get_canvas_screenshot`, `get_canvas_extent`, `set_canvas_extent` |
| **Processing** | `execute_processing`, `list_processing_algorithms`, `get_algorithm_help` |
| **Layouts** | `list_layouts`, `export_layout` |
| **Layer tree** | `get_layer_tree`, `create_layer_group`, `move_layer_to_group` |
| **Plugins** | `list_plugins`, `get_plugin_info`, `reload_plugin` |
| **System** | `ping`, `get_qgis_info`, `get_raster_info`, `get_message_log`, `execute_code`, `batch_commands`, `validate_expression`, `get_project_variables`, `set_project_variable`, `get_setting`, `set_setting`, `transform_coordinates` |

All tools are async with human-readable titles and annotations (`readOnly`, `destructive`, `idempotent`). Destructive tools ask for confirmation via MCP elicitation. Long-running tools report progress via MCP logging.

## Configuration

| Environment variable | Default | Description |
|---------------------|---------|-------------|
| `QGIS_MCP_HOST` | `localhost` | Host for socket connection |
| `QGIS_MCP_PORT` | `9876` | Port for socket connection |
| `QGIS_MCP_TRANSPORT` | `stdio` | MCP transport: `stdio` or `streamable-http` |

## Development

```bash
# Run unit tests (no QGIS needed — mocked socket)
uv run --no-sync pytest tests/test_mcp_tools.py -v

# Run integration tests (requires QGIS plugin running)
uv run --no-sync pytest tests/test_qgis_live.py -v
```

## License

This project is open source. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.
