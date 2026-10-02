# QGIS MCP HTTP API

## Endpoints

### GET /health
Verificar saúde do servidor

### GET /status
Status de conexão com QGIS

### GET /tools
Listar todas as 51 ferramentas

### POST /execute
Executar uma ferramenta QGIS

Exemplo:
```json
{
  "tool_name": "create_new_project",
  "arguments": {
    "name": "Meu Projeto"
  }
}
```
