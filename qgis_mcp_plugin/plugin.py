import base64
import contextlib
import fnmatch
import io
import json
import os
import socket
import struct
import sys
import traceback
from collections import deque
from datetime import UTC, datetime
from typing import ClassVar

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCategorizedSymbolRenderer,
    QgsClassificationEqualInterval,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeature,
    QgsFeatureRequest,
    QgsGeometry,
    QgsGraduatedSymbolRenderer,
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
    QgsLayoutExporter,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsMessageLog,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsRendererCategory,
    QgsSettings,
    QgsSingleSymbolRenderer,
    QgsStyle,
    QgsSymbol,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QBuffer, QByteArray, QObject, QSize, QTimer, QUrl, QVariant
from qgis.PyQt.QtGui import QColor, QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import (
    QAction,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)
from qgis.utils import active_plugins, available_plugins, pluginMetadata, reloadPlugin

from .compat import (
    AGG_ARRAY,
    AGG_COUNT,
    AGG_MAX,
    AGG_MEAN,
    AGG_MIN,
    AGG_STDEV,
    AGG_SUM,
    GEOM_LINE,
    GEOM_POLYGON,
    IODEVICE_WRITEONLY,
    LAYER_RASTER,
    LAYER_VECTOR,
    LAYOUT_SUCCESS,
    MSG_CRITICAL,
    MSG_WARNING,
    PROCESSING_OPTIONAL,
    RASTER_STATS_ALL,
    TOOLBUTTON_ICON_ONLY,
    TOOLBUTTON_MENU_POPUP,
)


class QgisMCPServer(QObject):
    """Server class to handle socket connections and execute QGIS commands"""

    def __init__(self, host="localhost", port=9876, iface=None):
        super().__init__()
        self.host = host
        self.port = port
        self.iface = iface
        self.running = False
        self.socket = None
        self.client = None
        self.buffer = b""
        self.timer = None
        self._message_log = deque(maxlen=1000)

    def start(self):
        """Start the server"""
        self.running = True
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)
            self.socket.setblocking(False)

            self.timer = QTimer()
            self.timer.timeout.connect(self.process_server)
            self.timer.start(25)  # 25ms interval

            QgsApplication.messageLog().messageReceived.connect(self._capture_message)
            QgsMessageLog.logMessage(
                f"QGIS MCP server started on {self.host}:{self.port}", "QGIS MCP"
            )
            return True
        except Exception as e:
            QgsMessageLog.logMessage(f"Failed to start server: {e!s}", "QGIS MCP", MSG_CRITICAL)
            self.stop()
            return False

    def stop(self):
        """Stop the server"""
        self.running = False

        with contextlib.suppress(Exception):
            QgsApplication.messageLog().messageReceived.disconnect(self._capture_message)

        if self.timer:
            self.timer.stop()
            self.timer = None

        if self.socket:
            self.socket.close()
        if self.client:
            self.client.close()

        self.socket = None
        self.client = None
        QgsMessageLog.logMessage("QGIS MCP server stopped", "QGIS MCP")

    def _send_response(self, response):
        """Send a length-prefixed JSON response to the client."""
        resp_bytes = json.dumps(response).encode("utf-8")
        header = struct.pack(">I", len(resp_bytes))
        self.client.sendall(header + resp_bytes)

    def process_server(self):
        """Process server operations (called by timer)"""
        if not self.running:
            return

        try:
            # Accept new connections
            if not self.client and self.socket:
                try:
                    self.client, address = self.socket.accept()
                    self.client.setblocking(False)
                    QgsMessageLog.logMessage(f"Connected to client: {address}", "QGIS MCP")
                except BlockingIOError:
                    pass
                except Exception as e:
                    QgsMessageLog.logMessage(
                        f"Error accepting connection: {e!s}", "QGIS MCP", MSG_WARNING
                    )

            # Process existing connection
            if self.client:
                try:
                    try:
                        data = self.client.recv(65536)
                        if data:
                            if len(self.buffer) + len(data) > 10 * 1024 * 1024:
                                raise ValueError("Buffer exceeded 10 MB limit")
                            self.buffer += data
                            # Process complete length-prefixed messages
                            while len(self.buffer) >= 4:
                                msg_len = struct.unpack(">I", self.buffer[:4])[0]
                                if msg_len > 10 * 1024 * 1024:  # 10 MB limit
                                    raise ValueError(f"Message too large: {msg_len} bytes")
                                if len(self.buffer) < 4 + msg_len:
                                    break  # Incomplete message
                                msg_bytes = self.buffer[4 : 4 + msg_len]
                                self.buffer = self.buffer[4 + msg_len :]
                                command = json.loads(msg_bytes.decode("utf-8"))
                                response = self.execute_command(command)
                                self._send_response(response)
                        else:
                            QgsMessageLog.logMessage("Client disconnected", "QGIS MCP")
                            self.client.close()
                            self.client = None
                            self.buffer = b""
                    except BlockingIOError:
                        pass
                    except Exception as e:
                        QgsMessageLog.logMessage(
                            f"Error receiving data: {e!s}", "QGIS MCP", MSG_WARNING
                        )
                        self.client.close()
                        self.client = None
                        self.buffer = b""

                except Exception as e:
                    QgsMessageLog.logMessage(f"Error with client: {e!s}", "QGIS MCP", MSG_WARNING)
                    if self.client:
                        self.client.close()
                        self.client = None
                    self.buffer = b""

        except Exception as e:
            QgsMessageLog.logMessage(f"Server error: {e!s}", "QGIS MCP", MSG_CRITICAL)

    def execute_command(self, command):
        """Execute a command"""
        try:
            cmd_type = command.get("type")
            params = command.get("params", {})

            handlers = {
                "ping": self.ping,
                "get_qgis_info": self.get_qgis_info,
                "load_project": self.load_project,
                "get_project_info": self.get_project_info,
                "execute_code": self.execute_code,
                "add_vector_layer": self.add_vector_layer,
                "add_raster_layer": self.add_raster_layer,
                "get_layers": self.get_layers,
                "remove_layer": self.remove_layer,
                "zoom_to_layer": self.zoom_to_layer,
                "get_layer_features": self.get_layer_features,
                "execute_processing": self.execute_processing,
                "save_project": self.save_project,
                "render_map_base64": self.render_map_base64,
                "create_new_project": self.create_new_project,
                "get_field_statistics": self.get_field_statistics,
                "set_layer_visibility": self.set_layer_visibility,
                "get_canvas_extent": self.get_canvas_extent,
                "set_canvas_extent": self.set_canvas_extent,
                "get_raster_info": self.get_raster_info,
                "get_layer_info": self.get_layer_info,
                "get_layer_schema": self.get_layer_schema,
                "batch": self.batch,
                # Phase 2 new handlers
                "add_features": self.add_features,
                "update_features": self.update_features,
                "delete_features": self.delete_features,
                "set_layer_style": self.set_layer_style,
                "select_features": self.select_features,
                "get_selection": self.get_selection,
                "clear_selection": self.clear_selection,
                "create_memory_layer": self.create_memory_layer,
                "list_processing_algorithms": self.list_processing_algorithms,
                "get_algorithm_help": self.get_algorithm_help,
                "find_layer": self.find_layer,
                "list_layouts": self.list_layouts,
                "export_layout": self.export_layout,
                # Phase 3 — Plugin development & system management
                "get_message_log": self.get_message_log,
                "list_plugins": self.list_plugins,
                "get_plugin_info": self.get_plugin_info,
                "reload_plugin": self.reload_plugin,
                "get_layer_tree": self.get_layer_tree,
                "create_layer_group": self.create_layer_group,
                "move_layer_to_group": self.move_layer_to_group,
                "set_layer_property": self.set_layer_property,
                "get_layer_extent": self.get_layer_extent,
                "get_project_variables": self.get_project_variables,
                "set_project_variable": self.set_project_variable,
                "validate_expression": self.validate_expression,
                "get_setting": self.get_setting,
                "set_setting": self.set_setting,
                # Phase 4 — MCP modernization
                "get_canvas_screenshot": self.get_canvas_screenshot,
                "transform_coordinates": self.transform_coordinates,
            }

            handler = handlers.get(cmd_type)
            if handler:
                try:
                    QgsMessageLog.logMessage(f"Executing handler for {cmd_type}", "QGIS MCP")
                    result = handler(**params)
                    QgsMessageLog.logMessage("Handler execution complete", "QGIS MCP")
                    return {"status": "success", "result": result}
                except Exception as e:
                    QgsMessageLog.logMessage(f"Error in handler: {e!s}", "QGIS MCP", MSG_CRITICAL)
                    return {"status": "error", "message": str(e)}
            else:
                return {"status": "error", "message": f"Unknown command type: {cmd_type}"}

        except Exception as e:
            QgsMessageLog.logMessage(f"Error executing command: {e!s}", "QGIS MCP", MSG_CRITICAL)
            return {"status": "error", "message": str(e)}

    # -----------------------------------------------------------------------
    # Command handlers
    # -----------------------------------------------------------------------

    def ping(self, **kwargs):
        return {"pong": True}

    def get_qgis_info(self, **kwargs):
        return {
            "qgis_version": Qgis.version(),
            "profile_folder": QgsApplication.qgisSettingsDirPath(),
            "plugins_count": len(active_plugins),
        }

    def get_project_info(self, **kwargs):
        project = QgsProject.instance()

        info = {
            "filename": project.fileName(),
            "title": project.title(),
            "layer_count": len(project.mapLayers()),
            "crs": project.crs().authid(),
            "layers": [],
        }

        layers = list(project.mapLayers().values())
        for i, layer in enumerate(layers):
            if i >= 10:
                break
            layer_info = {
                "id": layer.id(),
                "name": layer.name(),
                "type": self._get_layer_type(layer),
                "visible": layer.isValid()
                and project.layerTreeRoot().findLayer(layer.id()).isVisible(),
            }
            info["layers"].append(layer_info)

        return info

    def _get_layer_type(self, layer):
        if layer.type() == LAYER_VECTOR:
            return f"vector_{layer.geometryType()}"
        elif layer.type() == LAYER_RASTER:
            return "raster"
        else:
            return str(layer.type())

    def _convert_to_python_type(self, qvariant):
        if qvariant.isNull():
            return None
        value = qvariant.value()
        if isinstance(value, int | float | str | bool | type(None)):
            return value
        elif hasattr(value, "toPyDate"):
            return value.toPyDate().isoformat()
        elif hasattr(value, "toPyDateTime"):
            return value.toPyDateTime().isoformat()
        else:
            try:
                return str(value)
            except Exception:
                return None

    def _convert_attribute(self, value):
        """Convert a feature attribute value to a JSON-serializable type."""
        if isinstance(value, QVariant):
            return self._convert_to_python_type(value)
        if isinstance(value, int | float | str | bool | type(None)):
            return value
        try:
            return str(value)
        except Exception:
            return None

    def execute_code(self, code, **kwargs):
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        original_stdout = sys.stdout
        original_stderr = sys.stderr

        try:
            sys.stdout = stdout_capture
            sys.stderr = stderr_capture

            namespace = {
                "qgis": Qgis,
                "QgsProject": QgsProject,
                "iface": self.iface,
                "QgsApplication": QgsApplication,
                "QgsVectorLayer": QgsVectorLayer,
                "QgsRasterLayer": QgsRasterLayer,
                "QgsCoordinateReferenceSystem": QgsCoordinateReferenceSystem,
            }

            exec(code, namespace)

            return {
                "executed": True,
                "stdout": stdout_capture.getvalue(),
                "stderr": stderr_capture.getvalue(),
            }
        except Exception as e:
            error_traceback = traceback.format_exc()
            return {
                "executed": False,
                "error": str(e),
                "traceback": error_traceback,
                "stdout": stdout_capture.getvalue(),
                "stderr": stderr_capture.getvalue(),
            }
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

    def add_vector_layer(self, path, name=None, provider="ogr", **kwargs):
        if not name:
            name = os.path.basename(path)

        layer = QgsVectorLayer(path, name, provider)
        if not layer.isValid():
            raise Exception(f"Layer is not valid: {path}")

        QgsProject.instance().addMapLayer(layer)

        return {
            "id": layer.id(),
            "name": layer.name(),
            "type": self._get_layer_type(layer),
            "feature_count": layer.featureCount(),
        }

    def add_raster_layer(self, path, name=None, provider="gdal", **kwargs):
        if not name:
            name = os.path.basename(path)

        layer = QgsRasterLayer(path, name, provider)
        if not layer.isValid():
            raise Exception(f"Layer is not valid: {path}")

        QgsProject.instance().addMapLayer(layer)

        return {
            "id": layer.id(),
            "name": layer.name(),
            "type": "raster",
            "width": layer.width(),
            "height": layer.height(),
        }

    def get_layers(self, limit=50, offset=0, **kwargs):
        project = QgsProject.instance()
        all_layers = list(project.mapLayers().items())
        total_count = len(all_layers)
        page = all_layers[offset : offset + limit]

        layers = []
        for layer_id, layer in page:
            layer_info = {
                "id": layer_id,
                "name": layer.name(),
                "type": self._get_layer_type(layer),
                "visible": project.layerTreeRoot().findLayer(layer_id).isVisible(),
            }

            if layer.type() == LAYER_VECTOR:
                layer_info.update(
                    {"feature_count": layer.featureCount(), "geometry_type": layer.geometryType()}
                )
            elif layer.type() == LAYER_RASTER:
                layer_info.update({"width": layer.width(), "height": layer.height()})

            layers.append(layer_info)

        return {"layers": layers, "total_count": total_count, "offset": offset, "limit": limit}

    def remove_layer(self, layer_id, **kwargs):
        project = QgsProject.instance()
        if layer_id in project.mapLayers():
            project.removeMapLayer(layer_id)
            return {"ok": True}
        else:
            raise Exception(f"Layer not found: {layer_id}")

    def zoom_to_layer(self, layer_id, **kwargs):
        project = QgsProject.instance()
        if layer_id in project.mapLayers():
            layer = project.mapLayer(layer_id)
            self.iface.setActiveLayer(layer)
            self.iface.zoomToActiveLayer()
            return {"ok": True}
        else:
            raise Exception(f"Layer not found: {layer_id}")

    def get_layer_features(
        self, layer_id, limit=10, offset=0, expression=None, include_geometry=False, **kwargs
    ):
        project = QgsProject.instance()

        if layer_id not in project.mapLayers():
            raise Exception(f"Layer not found: {layer_id}")

        layer = project.mapLayer(layer_id)
        if layer.type() != LAYER_VECTOR:
            raise Exception(f"Layer is not a vector layer: {layer_id}")

        field_names = [field.name() for field in layer.fields()]
        feature_count = layer.featureCount()

        request = QgsFeatureRequest()
        if expression:
            request.setFilterExpression(expression)

        features = []
        skipped = 0
        for feature in layer.getFeatures(request):
            if skipped < offset:
                skipped += 1
                continue
            if len(features) >= limit:
                break

            # Phase 1C: Flatten to {"_fid": id, ...attrs} instead of nested "attributes"
            feature_obj = {"_fid": feature.id()}
            for field in layer.fields():
                feature_obj[field.name()] = self._convert_attribute(feature.attribute(field.name()))

            if include_geometry and feature.hasGeometry():
                geom = feature.geometry()
                geom_type = geom.type()

                try:
                    wkb_type_name = QgsWkbTypes.displayString(geom.wkbType())

                    if geom_type in [GEOM_POLYGON, GEOM_LINE]:
                        simplified_geom = geom.simplify(0.001)
                        points_count = len(simplified_geom.asWkt().split(","))
                        geom_obj = {
                            "type": geom_type,
                            "wkb_type": wkb_type_name,
                            "wkt_summary": f"{wkb_type_name} with {points_count} points",
                            "bbox": [
                                geom.boundingBox().xMinimum(),
                                geom.boundingBox().yMinimum(),
                                geom.boundingBox().xMaximum(),
                                geom.boundingBox().yMaximum(),
                            ],
                        }
                    else:
                        geom_obj = {
                            "type": geom_type,
                            "wkb_type": wkb_type_name,
                            "wkt": geom.asWkt(precision=3),
                        }
                except ImportError:
                    geom_obj = {
                        "type": geom_type,
                        "bbox": [
                            geom.boundingBox().xMinimum(),
                            geom.boundingBox().yMinimum(),
                            geom.boundingBox().xMaximum(),
                            geom.boundingBox().yMaximum(),
                        ]
                        if hasattr(geom, "boundingBox")
                        else None,
                        "wkt": geom.asWkt(precision=3),
                    }

                feature_obj["_geometry"] = geom_obj

            features.append(feature_obj)

        # Phase 1B: Stripped layer_id, layer_name, geometry_included
        return {
            "feature_count": feature_count,
            "fields": field_names,
            "features": features,
        }

    def get_field_statistics(self, layer_id, field_name, **kwargs):
        project = QgsProject.instance()
        if layer_id not in project.mapLayers():
            raise Exception(f"Layer not found: {layer_id}")

        layer = project.mapLayer(layer_id)
        if layer.type() != LAYER_VECTOR:
            raise Exception(f"Layer is not a vector layer: {layer_id}")

        field_idx = layer.fields().indexOf(field_name)
        if field_idx < 0:
            raise Exception(f"Field not found: {field_name}")

        field = layer.fields().at(field_idx)
        is_numeric = field.isNumeric()

        # Phase 1B: Stripped layer_id, field_name
        stats = {"is_numeric": is_numeric}

        if is_numeric:
            for stat_name, stat_enum in [
                ("count", AGG_COUNT),
                ("sum", AGG_SUM),
                ("mean", AGG_MEAN),
                ("min", AGG_MIN),
                ("max", AGG_MAX),
                ("stdev", AGG_STDEV),
            ]:
                val, ok = layer.aggregate(stat_enum, field_name)
                if ok:
                    stats[stat_name] = val
        else:
            count_val, ok = layer.aggregate(AGG_COUNT, field_name)
            if ok:
                stats["count"] = count_val
            distinct_val, ok = layer.aggregate(AGG_ARRAY, field_name)
            if ok and isinstance(distinct_val, list):
                unique = list(set(str(v) for v in distinct_val if v is not None))
                stats["distinct_count"] = len(unique)
                stats["distinct_values"] = unique[:50]

        return stats

    def set_layer_visibility(self, layer_id, visible, **kwargs):
        project = QgsProject.instance()
        if layer_id not in project.mapLayers():
            raise Exception(f"Layer not found: {layer_id}")

        tree_layer = project.layerTreeRoot().findLayer(layer_id)
        if not tree_layer:
            raise Exception(f"Layer not found in layer tree: {layer_id}")

        tree_layer.setItemVisibilityChecked(visible)
        # Phase 1B: Stripped layer_id, return only visible state
        return {"visible": visible}

    def get_canvas_extent(self, **kwargs):
        canvas = self.iface.mapCanvas()
        extent = canvas.extent()
        crs = canvas.mapSettings().destinationCrs()
        return {
            "xmin": extent.xMinimum(),
            "ymin": extent.yMinimum(),
            "xmax": extent.xMaximum(),
            "ymax": extent.yMaximum(),
            "crs": crs.authid(),
            "width": canvas.width(),
            "height": canvas.height(),
        }

    def set_canvas_extent(self, xmin, ymin, xmax, ymax, crs=None, **kwargs):
        canvas = self.iface.mapCanvas()
        rect = QgsRectangle(xmin, ymin, xmax, ymax)

        if crs:
            src_crs = QgsCoordinateReferenceSystem(crs)
            dst_crs = canvas.mapSettings().destinationCrs()
            if src_crs != dst_crs:
                transform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
                rect = transform.transformBoundingBox(rect)

        canvas.setExtent(rect)
        canvas.refresh()
        return {"extent": [rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum()]}

    def get_raster_info(self, layer_id, **kwargs):
        project = QgsProject.instance()
        if layer_id not in project.mapLayers():
            raise Exception(f"Layer not found: {layer_id}")

        layer = project.mapLayer(layer_id)
        if layer.type() != LAYER_RASTER:
            raise Exception(f"Layer is not a raster layer: {layer_id}")

        dp = layer.dataProvider()
        extent = layer.extent()

        # Phase 1B: Stripped layer_id, name
        info = {
            "width": layer.width(),
            "height": layer.height(),
            "band_count": layer.bandCount(),
            "crs": layer.crs().authid(),
            "extent": {
                "xmin": extent.xMinimum(),
                "ymin": extent.yMinimum(),
                "xmax": extent.xMaximum(),
                "ymax": extent.yMaximum(),
            },
            "bands": [],
        }

        for band in range(1, layer.bandCount() + 1):
            band_info = {"band": band}
            try:
                stats = dp.bandStatistics(band, RASTER_STATS_ALL)
                band_info.update(
                    {
                        "min": stats.minimumValue,
                        "max": stats.maximumValue,
                        "mean": stats.mean,
                        "stdev": stats.stdDev,
                    }
                )
            except Exception as e:
                QgsMessageLog.logMessage(
                    f"Could not compute stats for band {band}: {e}", "QGIS MCP", MSG_WARNING
                )
            nodata = dp.sourceNoDataValue(band)
            if nodata is not None:
                band_info["nodata"] = nodata
            info["bands"].append(band_info)

        return info

    def get_layer_info(self, layer_id, **kwargs):
        project = QgsProject.instance()
        if layer_id not in project.mapLayers():
            raise Exception(f"Layer not found: {layer_id}")

        layer = project.mapLayer(layer_id)
        extent = layer.extent()

        info = {
            "id": layer.id(),
            "name": layer.name(),
            "type": self._get_layer_type(layer),
            "crs": layer.crs().authid(),
            "extent": {
                "xmin": extent.xMinimum(),
                "ymin": extent.yMinimum(),
                "xmax": extent.xMaximum(),
                "ymax": extent.yMaximum(),
            },
            "source": layer.source(),
            "provider": layer.providerType(),
            "is_valid": layer.isValid(),
        }

        if layer.type() == LAYER_VECTOR:
            info["feature_count"] = layer.featureCount()
            info["geometry_type"] = layer.geometryType()
            info["fields"] = [
                {"name": f.name(), "type": f.typeName(), "length": f.length()}
                for f in layer.fields()
            ]
        elif layer.type() == LAYER_RASTER:
            info["width"] = layer.width()
            info["height"] = layer.height()
            info["band_count"] = layer.bandCount()

        return info

    def get_layer_schema(self, layer_id, **kwargs):
        project = QgsProject.instance()
        if layer_id not in project.mapLayers():
            raise Exception(f"Layer not found: {layer_id}")

        layer = project.mapLayer(layer_id)
        if layer.type() != LAYER_VECTOR:
            raise Exception(f"Layer is not a vector layer: {layer_id}")

        # Phase 1B: Stripped layer_id, layer_name
        return {
            "geometry_type": layer.geometryType(),
            "crs": layer.crs().authid(),
            "fields": [
                {
                    "name": f.name(),
                    "type": f.typeName(),
                    "length": f.length(),
                    "precision": f.precision(),
                    "is_numeric": f.isNumeric(),
                }
                for f in layer.fields()
            ],
        }

    def batch(self, commands, **kwargs):
        """Execute multiple commands in sequence, return array of results."""
        results = []
        for cmd in commands:
            cmd_type = cmd.get("type")
            params = cmd.get("params", {})
            result = self.execute_command({"type": cmd_type, "params": params})
            results.append(result)
        return results

    def execute_processing(self, algorithm, parameters, **kwargs):
        try:
            import processing

            result = processing.run(algorithm, parameters)
            return {"algorithm": algorithm, "result": {k: str(v) for k, v in result.items()}}
        except Exception as e:
            raise Exception(f"Processing error: {e!s}") from e

    def save_project(self, path=None, **kwargs):
        project = QgsProject.instance()

        if not path and not project.fileName():
            raise Exception("No project path specified and no current project path")

        save_path = path if path else project.fileName()
        if project.write(save_path):
            return {"saved": save_path}
        else:
            raise Exception(f"Failed to save project to {save_path}")

    def load_project(self, path, **kwargs):
        project = QgsProject.instance()
        if project.read(path):
            self.iface.mapCanvas().refresh()
            return {"loaded": path, "layer_count": len(project.mapLayers())}
        else:
            raise Exception(f"Failed to load project from {path}")

    def create_new_project(self, path, **kwargs):
        project = QgsProject.instance()
        if project.fileName():
            project.clear()
        project.setFileName(path)
        self.iface.mapCanvas().refresh()
        if project.write():
            return {
                "created": f"Project created and saved successfully at: {path}",
                "layer_count": len(project.mapLayers()),
            }
        else:
            raise Exception(f"Failed to save project to {path}")

    def render_map_base64(self, width=800, height=600, path=None, **kwargs):
        """Render the map and return base64-encoded PNG data."""
        try:
            ms = QgsMapSettings()
            layers = list(QgsProject.instance().mapLayers().values())
            ms.setLayers(layers)
            rect = self.iface.mapCanvas().extent()
            ms.setExtent(rect)
            ms.setOutputSize(QSize(width, height))
            ms.setBackgroundColor(QColor(255, 255, 255))
            ms.setOutputDpi(96)

            render = QgsMapRendererParallelJob(ms)
            render.start()
            render.waitForFinished()

            img = render.renderedImage()

            if path:
                img.save(path)

            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(IODEVICE_WRITEONLY)
            img.save(buf, "PNG")
            buf.close()
            b64 = base64.b64encode(bytes(ba)).decode("utf-8")

            return {"base64_data": b64, "mime_type": "image/png", "width": width, "height": height}

        except Exception as e:
            raise Exception(f"Render error: {e!s}") from e

    # -----------------------------------------------------------------------
    # Phase 2 new handlers
    # -----------------------------------------------------------------------

    def _get_vector_layer(self, layer_id):
        """Helper: get a vector layer or raise."""
        project = QgsProject.instance()
        if layer_id not in project.mapLayers():
            raise Exception(f"Layer not found: {layer_id}")
        layer = project.mapLayer(layer_id)
        if layer.type() != LAYER_VECTOR:
            raise Exception(f"Not a vector layer: {layer_id}")
        return layer

    def add_features(self, layer_id, features, **kwargs):
        layer = self._get_vector_layer(layer_id)
        dp = layer.dataProvider()
        qgs_features = []
        for feat_data in features:
            f = QgsFeature(layer.fields())
            attrs = feat_data.get("attributes", {})
            for field_name, value in attrs.items():
                idx = layer.fields().indexOf(field_name)
                if idx >= 0:
                    f.setAttribute(idx, value)
            wkt = feat_data.get("geometry_wkt")
            if wkt:
                f.setGeometry(QgsGeometry.fromWkt(wkt))
            qgs_features.append(f)

        ok, added = dp.addFeatures(qgs_features)
        if not ok:
            raise Exception("Failed to add features")
        layer.updateExtents()
        return {"added": len(added)}

    def update_features(self, layer_id, updates, **kwargs):
        layer = self._get_vector_layer(layer_id)
        dp = layer.dataProvider()
        attr_map = {}
        for upd in updates:
            fid = upd["fid"]
            attrs = upd.get("attributes", {})
            field_map = {}
            for field_name, value in attrs.items():
                idx = layer.fields().indexOf(field_name)
                if idx >= 0:
                    field_map[idx] = value
            if field_map:
                attr_map[fid] = field_map

        if attr_map:
            ok = dp.changeAttributeValues(attr_map)
            if not ok:
                raise Exception("Failed to update features")
        return {"updated": len(attr_map)}

    def delete_features(self, layer_id, fids=None, expression=None, **kwargs):
        layer = self._get_vector_layer(layer_id)
        dp = layer.dataProvider()

        if fids is not None:
            target_fids = fids
        elif expression:
            request = QgsFeatureRequest().setFilterExpression(expression)
            request.setNoAttributes()
            target_fids = [f.id() for f in layer.getFeatures(request)]
        else:
            raise Exception("Either fids or expression must be provided")

        ok = dp.deleteFeatures(target_fids)
        if not ok:
            raise Exception("Failed to delete features")
        layer.updateExtents()
        return {"deleted": len(target_fids)}

    def set_layer_style(
        self, layer_id, style_type, field=None, classes=5, color_ramp="Spectral", **kwargs
    ):
        layer = self._get_vector_layer(layer_id)

        if style_type == "single":
            symbol = QgsSymbol.defaultSymbol(layer.geometryType())
            renderer = QgsSingleSymbolRenderer(symbol)
            layer.setRenderer(renderer)

        elif style_type == "categorized":
            if not field:
                raise Exception("field is required for categorized style")
            idx = layer.fields().indexOf(field)
            if idx < 0:
                raise Exception(f"Field not found: {field}")

            unique_values = sorted(
                layer.uniqueValues(idx), key=lambda x: str(x) if x is not None else ""
            )
            ramp = QgsStyle.defaultStyle().colorRamp(color_ramp)
            if not ramp:
                ramp = QgsStyle.defaultStyle().colorRamp("Spectral")

            categories = []
            n = max(len(unique_values) - 1, 1)
            for i, value in enumerate(unique_values):
                symbol = QgsSymbol.defaultSymbol(layer.geometryType())
                symbol.setColor(ramp.color(i / n))
                label = str(value) if value is not None else "NULL"
                categories.append(QgsRendererCategory(value, symbol, label))

            renderer = QgsCategorizedSymbolRenderer(field, categories)
            layer.setRenderer(renderer)

        elif style_type == "graduated":
            if not field:
                raise Exception("field is required for graduated style")
            idx = layer.fields().indexOf(field)
            if idx < 0:
                raise Exception(f"Field not found: {field}")

            symbol = QgsSymbol.defaultSymbol(layer.geometryType())
            ramp = QgsStyle.defaultStyle().colorRamp(color_ramp)
            if not ramp:
                ramp = QgsStyle.defaultStyle().colorRamp("Spectral")

            renderer = QgsGraduatedSymbolRenderer(field)
            renderer.setSourceSymbol(symbol.clone())
            renderer.setSourceColorRamp(ramp)

            renderer.setClassificationMethod(QgsClassificationEqualInterval())
            renderer.updateClasses(layer, classes)

            layer.setRenderer(renderer)
        else:
            raise Exception(
                f"Unknown style_type: {style_type}. Use 'single', 'categorized', or 'graduated'"
            )

        layer.triggerRepaint()
        self.iface.layerTreeView().refreshLayerSymbology(layer.id())
        return {"ok": True}

    def select_features(self, layer_id, expression=None, fids=None, **kwargs):
        layer = self._get_vector_layer(layer_id)

        if fids is not None:
            layer.selectByIds(fids)
        elif expression:
            layer.selectByExpression(expression)
        else:
            raise Exception("Either fids or expression must be provided")

        return {"selected": layer.selectedFeatureCount()}

    def get_selection(self, layer_id, **kwargs):
        layer = self._get_vector_layer(layer_id)
        return {
            "fids": list(layer.selectedFeatureIds()),
            "count": layer.selectedFeatureCount(),
        }

    def clear_selection(self, layer_id, **kwargs):
        layer = self._get_vector_layer(layer_id)
        layer.removeSelection()
        return {"ok": True}

    def create_memory_layer(self, name, geometry_type, crs="EPSG:4326", fields=None, **kwargs):
        field_parts = []
        if fields:
            for f in fields:
                field_parts.append(f"field={f['name']}:{f['type']}")

        uri = f"{geometry_type}?crs={crs}"
        if field_parts:
            uri += "&" + "&".join(field_parts)

        layer = QgsVectorLayer(uri, name, "memory")
        if not layer.isValid():
            raise Exception(f"Failed to create memory layer: {uri}")

        QgsProject.instance().addMapLayer(layer)
        return {
            "id": layer.id(),
            "name": layer.name(),
            "type": self._get_layer_type(layer),
            "feature_count": 0,
        }

    def list_processing_algorithms(self, search=None, provider=None, **kwargs):
        registry = QgsApplication.processingRegistry()
        algorithms = []

        for alg in registry.algorithms():
            if provider and alg.provider().id() != provider:
                continue
            if search:
                search_lower = search.lower()
                if (
                    search_lower not in alg.id().lower()
                    and search_lower not in alg.displayName().lower()
                ):
                    continue
            algorithms.append(
                {
                    "id": alg.id(),
                    "name": alg.displayName(),
                    "provider": alg.provider().id(),
                }
            )

        return {"algorithms": algorithms, "count": len(algorithms)}

    def get_algorithm_help(self, algorithm_id, **kwargs):
        registry = QgsApplication.processingRegistry()
        alg = registry.algorithmById(algorithm_id)
        if not alg:
            raise Exception(f"Algorithm not found: {algorithm_id}")

        params = []
        for param in alg.parameterDefinitions():
            param_info = {
                "name": param.name(),
                "description": param.description(),
                "type": param.type(),
                "optional": bool(param.flags() & PROCESSING_OPTIONAL),
            }
            try:
                default = param.defaultValue()
                if default is not None:
                    param_info["default"] = str(default)
            except Exception:
                pass
            params.append(param_info)

        outputs = []
        for out in alg.outputDefinitions():
            outputs.append(
                {
                    "name": out.name(),
                    "description": out.description(),
                    "type": out.type(),
                }
            )

        return {
            "id": alg.id(),
            "name": alg.displayName(),
            "description": alg.shortDescription() or "",
            "provider": alg.provider().id(),
            "parameters": params,
            "outputs": outputs,
        }

    def find_layer(self, name_pattern, **kwargs):
        project = QgsProject.instance()
        matches = []
        pattern_lower = name_pattern.lower()
        for layer_id, layer in project.mapLayers().items():
            name_lower = layer.name().lower()
            if fnmatch.fnmatch(name_lower, pattern_lower) or pattern_lower in name_lower:
                matches.append(
                    {
                        "id": layer_id,
                        "name": layer.name(),
                        "type": self._get_layer_type(layer),
                    }
                )
        return {"layers": matches, "count": len(matches)}

    def list_layouts(self, **kwargs):
        manager = QgsProject.instance().layoutManager()
        layouts = []
        for layout in manager.layouts():
            layouts.append(
                {
                    "name": layout.name(),
                    "page_count": layout.pageCollection().pageCount(),
                }
            )
        return {"layouts": layouts, "count": len(layouts)}

    def export_layout(self, layout_name, path, format="pdf", dpi=300, **kwargs):
        manager = QgsProject.instance().layoutManager()
        layout = manager.layoutByName(layout_name)
        if not layout:
            raise Exception(f"Layout not found: {layout_name}")

        exporter = QgsLayoutExporter(layout)
        fmt = format.lower()

        if fmt == "pdf":
            settings = QgsLayoutExporter.PdfExportSettings()
            settings.dpi = dpi
            result = exporter.exportToPdf(path, settings)
        elif fmt in ("png", "jpg", "jpeg", "tif", "tiff", "bmp"):
            settings = QgsLayoutExporter.ImageExportSettings()
            settings.dpi = dpi
            result = exporter.exportToImage(path, settings)
        elif fmt == "svg":
            settings = QgsLayoutExporter.SvgExportSettings()
            settings.dpi = dpi
            result = exporter.exportToSvg(path, settings)
        else:
            raise Exception(f"Unsupported format: {format}")

        if result != LAYOUT_SUCCESS:
            raise Exception(f"Export failed with code: {result}")

        return {"ok": True, "path": path}

    # -----------------------------------------------------------------------
    # Phase 3 — Plugin development & system management handlers
    # -----------------------------------------------------------------------

    _LEVEL_MAP: ClassVar[dict[int, str]] = {0: "info", 1: "warning", 2: "critical", 3: "success"}

    def _capture_message(self, message, tag, level):
        """Capture a message log entry into the deque."""
        self._message_log.append(
            {
                "tag": tag,
                "message": message,
                "level": self._LEVEL_MAP.get(level, str(level)),
                "timestamp": datetime.now(tz=UTC).isoformat(),
            }
        )

    def get_message_log(self, level=None, tag=None, limit=100, **kwargs):
        entries = list(self._message_log)
        entries.reverse()  # newest first
        if level:
            entries = [e for e in entries if e["level"] == level]
        if tag:
            entries = [e for e in entries if e["tag"] == tag]
        entries = entries[:limit]
        return {"messages": entries, "count": len(entries)}

    def list_plugins(self, enabled_only=False, **kwargs):
        result = []
        names = list(active_plugins) if enabled_only else list(available_plugins)
        for name in sorted(names):
            result.append(
                {
                    "name": name,
                    "enabled": name in active_plugins,
                    "version": pluginMetadata(name, "version") or "",
                    "path": pluginMetadata(name, "path") or "",
                }
            )
        return {"plugins": result, "count": len(result)}

    def get_plugin_info(self, plugin_name, **kwargs):
        if plugin_name not in available_plugins and plugin_name not in active_plugins:
            raise Exception(f"Plugin not found: {plugin_name}")
        return {
            "name": plugin_name,
            "enabled": plugin_name in active_plugins,
            "version": pluginMetadata(plugin_name, "version") or "",
            "description": pluginMetadata(plugin_name, "description") or "",
            "author": pluginMetadata(plugin_name, "author") or "",
            "path": pluginMetadata(plugin_name, "path") or "",
        }

    def reload_plugin(self, plugin_name, **kwargs):
        if plugin_name == "qgis_mcp_plugin":
            raise Exception("Cannot reload MCP plugin (would break the connection)")
        if plugin_name not in active_plugins:
            raise Exception(f"Plugin not active: {plugin_name}")
        reloadPlugin(plugin_name)
        return {"reloaded": plugin_name, "ok": True}

    def _layer_tree_node(self, node):
        """Recursively build a dict for a layer tree node."""
        if isinstance(node, QgsLayerTreeGroup):
            children = [self._layer_tree_node(c) for c in node.children()]
            result = {
                "type": "group",
                "name": node.name(),
                "visible": node.isVisible(),
                "children": children,
            }
            return result
        elif isinstance(node, QgsLayerTreeLayer):
            layer = node.layer()
            result = {
                "type": "layer",
                "name": node.name(),
                "visible": node.isVisible(),
            }
            if layer:
                result["layer_id"] = layer.id()
                result["layer_type"] = self._get_layer_type(layer)
            return result
        return {"type": "unknown", "name": str(node)}

    def get_layer_tree(self, **kwargs):
        root = QgsProject.instance().layerTreeRoot()
        children = [self._layer_tree_node(c) for c in root.children()]
        return {"children": children}

    def create_layer_group(self, name, parent=None, **kwargs):
        root = QgsProject.instance().layerTreeRoot()
        if parent:
            target = root.findGroup(parent)
            if not target:
                raise Exception(f"Parent group not found: {parent}")
        else:
            target = root
        target.addGroup(name)
        return {"name": name, "ok": True}

    def move_layer_to_group(self, layer_id, group_name, **kwargs):
        project = QgsProject.instance()
        root = project.layerTreeRoot()

        node = root.findLayer(layer_id)
        if not node:
            raise Exception(f"Layer not found in tree: {layer_id}")

        target = root.findGroup(group_name)
        if not target:
            raise Exception(f"Group not found: {group_name}")

        clone = node.clone()
        target.addChildNode(clone)
        node.parent().removeChildNode(node)
        return {"ok": True}

    def set_layer_property(self, layer_id, property, value, **kwargs):
        project = QgsProject.instance()
        if layer_id not in project.mapLayers():
            raise Exception(f"Layer not found: {layer_id}")

        layer = project.mapLayer(layer_id)

        if property == "opacity":
            layer.setOpacity(float(value))
        elif property == "name":
            layer.setName(str(value))
        elif property == "scale_visibility":
            layer.setScaleBasedVisibility(bool(value))
        elif property == "min_scale":
            layer.setMinimumScale(float(value))
        elif property == "max_scale":
            layer.setMaximumScale(float(value))
        else:
            raise Exception(
                f"Unknown property: {property}. "
                "Supported: opacity, name, min_scale, max_scale, scale_visibility"
            )

        self.iface.mapCanvas().refresh()
        return {"ok": True, "property": property, "value": value}

    def get_layer_extent(self, layer_id, **kwargs):
        project = QgsProject.instance()
        if layer_id not in project.mapLayers():
            raise Exception(f"Layer not found: {layer_id}")

        layer = project.mapLayer(layer_id)
        extent = layer.extent()
        return {
            "xmin": extent.xMinimum(),
            "ymin": extent.yMinimum(),
            "xmax": extent.xMaximum(),
            "ymax": extent.yMaximum(),
            "crs": layer.crs().authid(),
        }

    def get_project_variables(self, **kwargs):
        scope = QgsExpressionContextUtils.projectScope(QgsProject.instance())
        variables = {}
        for name in scope.variableNames():
            variables[name] = scope.variable(name)
        return {"variables": variables}

    def set_project_variable(self, key, value, **kwargs):
        QgsExpressionContextUtils.setProjectVariable(QgsProject.instance(), key, value)
        return {"ok": True, "key": key, "value": value}

    def validate_expression(self, expression, layer_id=None, **kwargs):
        expr = QgsExpression(expression)
        result = {
            "valid": not expr.hasParserError(),
            "referenced_columns": list(expr.referencedColumns()),
        }
        if expr.hasParserError():
            result["error"] = expr.parserErrorString()

        if layer_id:
            project = QgsProject.instance()
            if layer_id in project.mapLayers():
                layer = project.mapLayer(layer_id)
                if layer.type() == LAYER_VECTOR:
                    context = QgsExpressionContext()
                    context.appendScope(QgsExpressionContextUtils.layerScope(layer))
                    expr.prepare(context)
                    if expr.hasEvalError():
                        result["eval_error"] = expr.evalErrorString()

        return result

    def get_setting(self, key, **kwargs):
        settings = QgsSettings()
        value = settings.value(key)
        return {
            "key": key,
            "value": value,
            "exists": settings.contains(key),
        }

    def set_setting(self, key, value, **kwargs):
        settings = QgsSettings()
        settings.setValue(key, value)
        return {"ok": True, "key": key}

    # -----------------------------------------------------------------------
    # Phase 4 — MCP modernization handlers
    # -----------------------------------------------------------------------

    def get_canvas_screenshot(self, **kwargs):
        """Grab the current map canvas as a fast screenshot (no re-render)."""
        canvas = self.iface.mapCanvas()
        pixmap = canvas.grab()
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(IODEVICE_WRITEONLY)
        pixmap.save(buf, "PNG")
        buf.close()
        b64 = base64.b64encode(ba.data()).decode("ascii")
        return {
            "base64_data": b64,
            "mime_type": "image/png",
            "width": pixmap.width(),
            "height": pixmap.height(),
        }

    def transform_coordinates(
        self, source_crs, target_crs, point=None, points=None, bbox=None, **kwargs
    ):
        """Transform coordinates between coordinate reference systems."""
        src = QgsCoordinateReferenceSystem(source_crs)
        dst = QgsCoordinateReferenceSystem(target_crs)
        if not src.isValid():
            raise Exception(f"Invalid source CRS: {source_crs}")
        if not dst.isValid():
            raise Exception(f"Invalid target CRS: {target_crs}")

        xform = QgsCoordinateTransform(src, dst, QgsProject.instance())
        result = {"source_crs": source_crs, "target_crs": target_crs}

        if point:
            pt = xform.transform(QgsPointXY(point["x"], point["y"]))
            result["point"] = {"x": pt.x(), "y": pt.y()}

        if points:
            transformed = []
            for p in points:
                pt = xform.transform(QgsPointXY(p["x"], p["y"]))
                transformed.append({"x": pt.x(), "y": pt.y()})
            result["points"] = transformed

        if bbox:
            rect = QgsRectangle(bbox["xmin"], bbox["ymin"], bbox["xmax"], bbox["ymax"])
            transformed_rect = xform.transformBoundingBox(rect)
            result["bbox"] = {
                "xmin": transformed_rect.xMinimum(),
                "ymin": transformed_rect.yMinimum(),
                "xmax": transformed_rect.xMaximum(),
                "ymax": transformed_rect.yMaximum(),
            }

        return result


class QgisMCPPlugin:
    """Main plugin class for QGIS MCP"""

    REPO_URL = "https://github.com/nkarasiak/qgis-mcp"

    def __init__(self, iface):
        self.iface = iface
        self.server = None
        self.action = None
        self.help_action = None
        self.tool_button = None
        self._toolbar_action = None  # the action wrapping the tool button

    def _logo_icon(self):
        """Load the MCP logo from the plugin directory."""
        icon_path = os.path.join(os.path.dirname(__file__), "icons", "icon.png")
        return QIcon(icon_path)

    def initGui(self):
        toolbar = self.iface.pluginToolBar()

        # Main action (used for menu entry + click handler)
        self.action = QAction(self._logo_icon(), "Run MCP", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setToolTip("Start MCP server on port 9876")
        self.action.triggered.connect(self.toggle_server)

        # Port config in dropdown menu
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(9876)
        self.port_spin.setPrefix("Port: ")

        port_widget = QWidget()
        port_layout = QHBoxLayout()
        port_layout.setContentsMargins(6, 4, 6, 4)
        port_layout.addWidget(self.port_spin)
        port_widget.setLayout(port_layout)

        port_wa = QWidgetAction(self.iface.mainWindow())
        port_wa.setDefaultWidget(port_widget)

        menu = QMenu()
        menu.addAction(port_wa)

        # Tool button with dropdown (like Plugin Reloader)
        self.tool_button = QToolButton()
        self.tool_button.setDefaultAction(self.action)
        self.tool_button.setMenu(menu)
        self.tool_button.setPopupMode(TOOLBUTTON_MENU_POPUP)
        self.tool_button.setToolButtonStyle(TOOLBUTTON_ICON_ONLY)
        self._toolbar_action = toolbar.addWidget(self.tool_button)

        self.help_action = QAction("Help / Install MCP Server", self.iface.mainWindow())
        self.help_action.triggered.connect(self._show_help)

        self.iface.addPluginToMenu("QGIS MCP", self.action)
        self.iface.addPluginToMenu("QGIS MCP", self.help_action)

    def _green_logo_icon(self):
        """Load the green MCP logo for active state."""
        icon_path = os.path.join(os.path.dirname(__file__), "icons", "icon_active.png")
        return QIcon(icon_path)

    def _show_help(self):
        """Show help dialog with MCP server installation instructions."""
        dlg = QDialog(self.iface.mainWindow())
        dlg.setWindowTitle("QGIS MCP — Setup Guide")
        dlg.setMinimumWidth(520)

        layout = QVBoxLayout()
        label = QLabel(
            "<p>This plugin is only one half of the setup. You also need an "
            "<b>MCP server</b> so that Claude (or another LLM) can talk to QGIS.</p>"
            "<h3>Claude Code (one-liner)</h3>"
            "<pre>claude mcp add --transport stdio qgis-mcp \\\n"
            f"  -- uvx --from git+{self.REPO_URL} \\\n"
            "  qgis-mcp-server</pre>"
            "<h3>Claude Desktop</h3>"
            "<p>Add to your MCP config "
            "(<code>Settings &gt; Developer &gt; Edit Config</code>):</p>"
            '<pre>{\n'
            '  "mcpServers": {\n'
            '    "qgis": {\n'
            '      "command": "uvx",\n'
            '      "args": [\n'
            '        "--from",\n'
            f'        "git+{self.REPO_URL}",\n'
            '        "qgis-mcp-server"\n'
            "      ]\n"
            "    }\n"
            "  }\n"
            "}</pre>"
            "<p>Requires <a href=\"https://docs.astral.sh/uv/\">uv</a> "
            "to be installed.</p>"
            "<p>Full instructions on the "
            f'<a href="{self.REPO_URL}">GitHub repository</a>.</p>'
        )
        label.setWordWrap(True)
        label.setOpenExternalLinks(True)
        layout.addWidget(label)

        btn_layout = QHBoxLayout()
        github_btn = QToolButton()
        github_btn.setText("Open GitHub")
        github_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(self.REPO_URL))
        )
        btn_layout.addWidget(github_btn)
        btn_layout.addStretch()
        ok_btn = QToolButton()
        ok_btn.setText("OK")
        ok_btn.setMinimumWidth(80)
        ok_btn.clicked.connect(dlg.accept)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

        dlg.setLayout(layout)
        dlg.exec()

    def toggle_server(self, checked):
        if checked:
            port = self.port_spin.value()
            self.server = QgisMCPServer(port=port, iface=self.iface)
            if self.server.start():
                self.action.setIcon(self._green_logo_icon())
                self.action.setText(f"MCP :{port}")
                self.action.setToolTip(f"MCP server running on :{port} — click to stop")
                self.port_spin.setEnabled(False)
            else:
                self.server = None
                self.action.setChecked(False)
        else:
            if self.server:
                self.server.stop()
                self.server = None
            self.action.setIcon(self._logo_icon())
            self.action.setText("Run MCP")
            self.action.setToolTip("Start MCP server")
            self.port_spin.setEnabled(True)

    def unload(self):
        if self.server:
            self.server.stop()
            self.server = None
        if self.action:
            self.action.triggered.disconnect(self.toggle_server)
            self.iface.removePluginMenu("QGIS MCP", self.action)
            self.action = None
        if self.help_action:
            self.help_action.triggered.disconnect(self._show_help)
            self.iface.removePluginMenu("QGIS MCP", self.help_action)
            self.help_action = None
        if self._toolbar_action:
            self.iface.pluginToolBar().removeAction(self._toolbar_action)
            self._toolbar_action = None


# Plugin entry point
def classFactory(iface):
    return QgisMCPPlugin(iface)
