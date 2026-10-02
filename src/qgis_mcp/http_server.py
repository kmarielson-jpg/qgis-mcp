#!/usr/bin/env python3
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="QGIS MCP HTTP API",
    description="API REST para controlar QGIS via GenSpark",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ToolParameter(BaseModel):
    name: str
    type: str
    description: str
    required: bool = False
    default: Optional[Any] = None

class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: List[ToolParameter]

class ToolCall(BaseModel):
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)

class ToolResponse(BaseModel):
    success: bool
    tool_name: str
    result: Any = None
    error: Optional[str] = None
    timestamp: str

class ConnectionStatus(BaseModel):
    connected: bool
    qgis_version: Optional[str] = None
    mcp_server_status: str
    http_server_status: str = "online"

TOOLS_CATALOG = [
    {"name": "ping", "description": "Testa conexão com servidor"},
    {"name": "diagnose", "description": "Diagnóstico do sistema"},
    {"name": "get_qgis_info", "description": "Informações do QGIS"},
    {"name": "create_new_project", "description": "Criar novo projeto"},
    {"name": "open_project", "description": "Abrir projeto existente"},
    {"name": "save_project", "description": "Salvar projeto"},
    {"name": "get_project_info", "description": "Obter informações do projeto"},
    {"name": "get_layers", "description": "Listar todas as camadas"},
    {"name": "add_vector_layer", "description": "Adicionar camada vetorial"},
    {"name": "add_raster_layer", "description": "Adicionar camada raster"},
    {"name": "remove_layer", "description": "Remover camada"},
    {"name": "set_layer_visibility", "description": "Controlar visibilidade da camada"},
    {"name": "reorder_layers", "description": "Reorganizar ordem das camadas"},
    {"name": "get_features", "description": "Obter feições de uma camada"},
    {"name": "add_feature", "description": "Adicionar feição"},
    {"name": "update_feature", "description": "Atualizar feição"},
    {"name": "delete_feature", "description": "Deletar feição"},
    {"name": "get_feature_attributes", "description": "Obter atributos da feição"},
    {"name": "set_symbology", "description": "Aplicar simbologia à camada"},
    {"name": "set_layer_style", "description": "Aplicar estilo de camada"},
    {"name": "apply_color_ramp", "description": "Aplicar rampa de cores"},
    {"name": "run_algorithm", "description": "Executar algoritmo de processamento"},
    {"name": "buffer", "description": "Criar buffer"},
    {"name": "intersect", "description": "Calcular interseção"},
    {"name": "union", "description": "Calcular união"},
    {"name": "dissolve", "description": "Dissolver feições"},
    {"name": "clip", "description": "Recortar camada"},
    {"name": "merge", "description": "Mesclar camadas"},
    {"name": "get_layer_extent", "description": "Obter extensão da camada"},
    {"name": "get_layer_crs", "description": "Obter CRS da camada"},
    {"name": "set_layer_crs", "description": "Definir CRS da camada"},
    {"name": "get_feature_count", "description": "Contar feições"},
    {"name": "get_field_statistics", "description": "Estatísticas de campo"},
    {"name": "render_map", "description": "Renderizar mapa"},
    {"name": "export_map_image", "description": "Exportar mapa como imagem"},
    {"name": "set_zoom_level", "description": "Definir nível de zoom"},
    {"name": "pan_map", "description": "Navegar mapa"},
    {"name": "get_map_extent", "description": "Obter extensão do mapa"},
    {"name": "select_features", "description": "Selecionar feições"},
    {"name": "get_selected_features", "description": "Obter feições selecionadas"},
    {"name": "clear_selection", "description": "Limpar seleção"},
    {"name": "start_editing", "description": "Iniciar modo edição"},
    {"name": "stop_editing", "description": "Parar modo edição"},
    {"name": "commit_changes", "description": "Confirmar alterações"},
    {"name": "rollback_changes", "description": "Desfazer alterações"},
    {"name": "load_plugin", "description": "Carregar plugin"},
    {"name": "unload_plugin", "description": "Descarregar plugin"},
    {"name": "get_installed_plugins", "description": "Listar plugins instalados"},
]

@app.get("/health", tags=["Sistema"])
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "QGIS MCP HTTP API"
    }

@app.get("/status", tags=["Sistema"], response_model=ConnectionStatus)
async def connection_status():
    return ConnectionStatus(
        connected=True,
        qgis_version="3.28+",
        mcp_server_status="connected",
        http_server_status="online"
    )

@app.get("/tools", tags=["Ferramentas"])
async def list_tools():
    return {
        "total_tools": len(TOOLS_CATALOG),
        "tools": TOOLS_CATALOG
    }

@app.get("/tools/{tool_name}", tags=["Ferramentas"])
async def get_tool(tool_name: str):
    tool = next((t for t in TOOLS_CATALOG if t["name"] == tool_name), None)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Ferramenta '{tool_name}' não encontrada")
    return tool

@app.post("/execute", tags=["Execução"], response_model=ToolResponse)
async def execute_tool(tool_call: ToolCall):
    logger.info(f"Executando ferramenta: {tool_call.tool_name}")
    
    tool = next((t for t in TOOLS_CATALOG if t["name"] == tool_call.tool_name), None)
    if not tool:
        return ToolResponse(
            success=False,
            tool_name=tool_call.tool_name,
            error=f"Ferramenta '{tool_call.tool_name}' não encontrada",
            timestamp=datetime.utcnow().isoformat()
        )
    
    result = await execute_tool_mock(tool_call.tool_name, tool_call.arguments)
    
    return ToolResponse(
        success=True,
        tool_name=tool_call.tool_name,
        result=result,
        timestamp=datetime.utcnow().isoformat()
    )

async def execute_tool_mock(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    mock_responses = {
        "ping": {"message": "pong", "timestamp": datetime.utcnow().isoformat()},
        "get_qgis_info": {
            "version": "3.28",
            "release_name": "Firenze",
            "platform": "Linux",
            "python_version": "3.11"
        },
        "get_project_info": {
            "title": "Projeto QGIS",
            "file_path": "/path/to/project.qgz",
            "layer_count": 5,
            "crs": "EPSG:4326"
        },
        "get_layers": {
            "layers": []
        }
    }
    
    return mock_responses.get(tool_name, {"status": "executed", "tool": tool_name})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
