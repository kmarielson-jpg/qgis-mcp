@echo off
echo ========================================
echo  QGIS MCP - Iniciar Servidor MCP
echo  Para uso com Perplexity / Claude
echo ========================================
echo.

set QGIS_MCP_DIR=%~dp0
cd /d "%QGIS_MCP_DIR%"

:: Porta padrao 9876 - deve ser a mesma configurada no plugin QGIS
set QGIS_MCP_HOST=localhost
set QGIS_MCP_PORT=9876

echo [INFO] Certifique-se que o QGIS esta aberto e o plugin MCP ativo!
echo [INFO] Plugin: Complementos > QGIS MCP > Iniciar Servidor
echo [INFO] Host: %QGIS_MCP_HOST%  Porta: %QGIS_MCP_PORT%
echo.
echo [INFO] Iniciando servidor MCP...
echo  Deixe esta janela aberta enquanto usar o Perplexity com QGIS.
echo.

uv run --no-sync src/qgis_mcp/server.py

if %errorlevel% neq 0 (
    echo.
    echo [ERRO] Servidor encerrado com erro.
    echo Verifique se o QGIS esta aberto e o plugin MCP ativo.
)

pause
