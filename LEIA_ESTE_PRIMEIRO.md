# QGIS MCP - Guia de Configuracao para Windows

> Repositorio fork de [nkarasiak/qgis-mcp](https://github.com/nkarasiak/qgis-mcp)
> Configurado para uso com **Perplexity** no Windows.

---

## PASSO 1 - Pre-requisitos

Instale antes de comecar:

1. **QGIS** (3.28 ou mais novo): https://qgis.org/download/
2. **Git para Windows**: https://git-scm.com/download/win
3. **Python 3.12+**: https://www.python.org/downloads/
4. **uv** (gerenciador de pacotes rapido):
   - Abra o PowerShell e rode:
   ```
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
   - Feche e reabra o terminal apos instalar.

---

## PASSO 2 - Clonar e instalar

Abra o PowerShell na pasta onde quer instalar e rode:

```powershell
git clone https://github.com/kmarielson-jpg/qgis-mcp.git
cd qgis-mcp
```

Depois execute o instalador:

```
instalar_windows.bat
```

Ele ira:
- Instalar as dependencias Python automaticamente
- Criar link do plugin dentro do perfil QGIS

---

## PASSO 3 - Ativar o plugin no QGIS

1. Abra o **QGIS**
2. Va em: `Complementos` > `Gerenciar e Instalar Complementos`
3. Pesquise: **QGIS MCP**
4. Marque a caixa para ativar
5. Na barra de ferramentas, clique no icone do QGIS MCP
6. Clique em **"Iniciar Servidor"**
7. Deixe o QGIS aberto com o servidor rodando

---

## PASSO 4 - Iniciar o servidor MCP

Com o QGIS aberto e o plugin ativo, clique duas vezes em:

```
iniciar_servidor_mcp.bat
```

Deixe esta janela do CMD **aberta** enquanto usar o Perplexity.

---

## PASSO 5 - Configurar o Perplexity (MCP Local)

### No app Perplexity Desktop (Windows):

1. Va em: `Perfil` > `Settings` > `Connectors`
2. Clique em **"Add Connector"**
3. Escolha: **Local MCP Server**
4. Preencha:
   - **Name**: `QGIS MCP`
   - **Command**: Cole o caminho completo, por exemplo:
     ```
     C:\Users\SeuNome\qgis-mcp\iniciar_servidor_mcp.bat
     ```
     (ajuste para o caminho onde voce clonou o repositorio)
5. Clique em **Save**
6. Aguarde o status ficar **"Running"**

---

## PASSO 6 - Testar a conexao

Na janela do Perplexity, ative o conector QGIS MCP nas Sources e envie:

> "Use a ferramenta QGIS para fazer um ping e verificar a conexao"

Se retornar `pong` ou status OK, a conexao esta funcionando!

---

## Exemplos de comandos uteis para o Perplexity usar com QGIS

```
"Liste todas as camadas abertas no meu projeto QGIS"
"Crie um novo projeto QGIS e salve em C:/projetos/meu_projeto.qgz"
"Adicione a camada vetorial C:/dados/municipios.shp ao QGIS"
"Execute o algoritmo de buffer de 500m na camada municipios"
"Renderize o mapa atual e me mostre uma imagem"
"Execute o algoritmo fix geometries na camada ativa"
```

---

## Portas e configuracao

| Parametro | Valor padrao |
|-----------|-------------|
| Host | localhost |
| Porta | 9876 |
| Transporte | stdio |

> A porta no plugin QGIS MCP e a porta no script devem ser iguais.

---

## Solucao de problemas

| Problema | Solucao |
|----------|---------|
| Servidor nao inicia | Verifique se `uv` esta instalado e no PATH |
| Plugin nao aparece no QGIS | Rode `instalar_windows.bat` como Administrador |
| Perplexity nao conecta | Verifique se o QGIS esta aberto e o servidor rodando |
| Erro de porta | Confirme que porta 9876 esta livre (nao usada por outro programa) |
