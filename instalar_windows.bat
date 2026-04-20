@echo off
echo ========================================
echo  QGIS MCP - Instalador para Windows
echo  Perplexity + QGIS via MCP
echo ========================================
echo.

:: Verifica se uv esta instalado
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Instalando gerenciador uv...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    echo [OK] uv instalado! Reinicie o terminal e rode este script novamente.
    pause
    exit /b
)

echo [OK] uv encontrado.
echo.

:: Define o diretorio do repositorio (onde este .bat esta)
set QGIS_MCP_DIR=%~dp0
cd /d "%QGIS_MCP_DIR%"

echo [INFO] Diretorio: %QGIS_MCP_DIR%
echo.

:: Instala dependencias via uv
echo [INFO] Instalando dependencias Python...
uv sync
if %errorlevel% neq 0 (
    echo [ERRO] Falha ao instalar dependencias.
    pause
    exit /b 1
)
echo [OK] Dependencias instaladas.
echo.

:: Cria link simbolico do plugin no perfil QGIS
echo [INFO] Instalando plugin no QGIS...
python install.py
if %errorlevel% neq 0 (
    echo [AVISO] Instalacao automatica do plugin falhou. Instale manualmente pelo QGIS.
)
echo.

echo ========================================
echo  Instalacao concluida!
echo.
echo  PROXIMOS PASSOS:
echo  1. Abra o QGIS
echo  2. Va em Complementos ^> Gerenciar e Instalar
echo  3. Pesquise "QGIS MCP" e ative
echo  4. Clique em "Iniciar Servidor" na toolbar
echo  5. Use iniciar_servidor_mcp.bat para subir o MCP
echo ========================================
pause
