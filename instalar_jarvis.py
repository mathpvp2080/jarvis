"""Passos de instalação que o `instalar.bat` chama (Windows, com o Python do .venv).

O `.bat` faz o que só existe lá (achar o Python, criar o `.venv`, instalar as
dependências). O que precisa de julgamento fica aqui:

* os modelos da câmera (baixar, se faltarem);
* o Ollama (achar, e baixar o modelo de conversa);
* o Tesseract (avisar, porque é instalador separado).

Deixar isso em Python (e não em batch) permite testar as decisões em qualquer
sistema, como manda a convenção do projeto.

    python instalar_jarvis.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

# Usado quando o config ainda não existe.
MODELO_PADRAO = "qwen2.5:3b"

PAGINA_DO_OLLAMA = "https://ollama.com/download"
PAGINA_DO_TESSERACT = "https://github.com/UB-Mannheim/tesseract/wiki"


def modelo_do_config(pasta: Path = RAIZ) -> str:
    """O modelo de conversa configurado (ou o padrão, se não houver config)."""

    try:
        dados = json.loads((pasta / "config_jarvis.json").read_text(encoding="utf-8"))
    except Exception:
        return MODELO_PADRAO

    if isinstance(dados, dict):
        modelo = str(dados.get("modelo_local") or "").strip()

        if modelo:
            return modelo

    return MODELO_PADRAO


def inventario_do_ollama(saida: str) -> set:
    """Lê a lista do `ollama list` e devolve os nomes dos modelos.

    A saída é uma tabela:

        NAME                    ID              SIZE      MODIFIED
        qwen2.5:3b              abc123          1.9 GB    2 days ago
    """

    modelos = set()

    for linha in str(saida or "").splitlines()[1:]:
        colunas = linha.split()

        if colunas:
            modelos.add(colunas[0])

    return modelos


def falta_baixar(inventario: set, modelo: str) -> bool:
    """O modelo pedido já está baixado?

    Tolera a diferença de tag: quem digita "qwen2.5" quer o "qwen2.5:latest"
    que o Ollama mostra, e vice-versa.
    """

    modelo = str(modelo or "").strip()

    if not modelo:
        return False

    if modelo in inventario:
        return False

    sem_tag = modelo.split(":")[0]
    return not any(nome.split(":")[0] == sem_tag for nome in inventario)


def _logar(mensagem=""):
    print(mensagem, flush=True)


def garantir_modelos_da_camera(pasta: Path = RAIZ, log=_logar) -> bool:
    """Baixa o que faltar dos modelos da câmera. Nunca derruba a instalação."""

    try:
        from preparar_modelos import garantir_modelos

        faltando = garantir_modelos(pasta, baixar=True, log=log)

        if faltando:
            log(
                "  [aviso] Não consegui baixar: "
                + ", ".join(faltando)
                + " (a câmera avisa no painel; dá para baixar depois com "
                "python preparar_modelos.py)"
            )

        return not faltando
    except Exception as erro:
        log(f"  [aviso] Modelos da câmera: {type(erro).__name__}: {erro}")
        return False


def garantir_modelo_do_ollama(
    modelo: str = "",
    log=_logar,
    achar=shutil.which,
    executar=subprocess.run,
    abrir=webbrowser.open,
) -> bool:
    """Confere o Ollama e baixa o modelo de conversa, se faltar.

    Devolve ``False`` quando o Ollama não está instalado — o Jarvis funciona
    sem ele (só perde a conversa e o cérebro com ferramentas), então isto é
    aviso, não erro.
    """

    modelo = modelo or MODELO_PADRAO
    caminho = achar("ollama")

    if not caminho and os.name == "nt":
        padrao = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"

        if padrao.exists():
            caminho = str(padrao)

    if not caminho:
        log("  [aviso] O Ollama não está instalado.")
        log("          Sem ele a Jarvis conversa e não entende pedidos novos.")
        log(f"          Baixe em: {PAGINA_DO_OLLAMA} (depois rode o instalar.bat de novo)")

        try:
            abrir(PAGINA_DO_OLLAMA)
        except Exception:
            pass

        return False

    log(f"  Ollama encontrado em {caminho}")

    try:
        saida = executar(
            [caminho, "list"], capture_output=True, text=True, timeout=30
        ).stdout
    except Exception as erro:
        log(f"  [aviso] Não consegui listar os modelos do Ollama: {erro}")
        return True

    if not falta_baixar(inventario_do_ollama(saida), modelo):
        log(f"  Modelo de conversa já instalado: {modelo}")
        return True

    log(f"  Baixando o modelo de conversa ({modelo}) — alguns minutos...")

    try:
        resultado = executar([caminho, "pull", modelo], timeout=3600)
        sucesso = getattr(resultado, "returncode", 0) == 0
    except Exception as erro:
        log(f"  [aviso] Não consegui baixar {modelo}: {erro}")
        return True

    log(f"  Modelo {modelo} {'pronto' if sucesso else 'não baixou; tente: ollama pull ' + modelo}")

    return True


def avisar_do_tesseract(log=_logar, achar=shutil.which) -> bool:
    """O Tesseract é instalador separado; sem ele, o clique assistido não lê tela."""

    if achar("tesseract"):
        log("  Tesseract encontrado (leitura de tela funcionando).")
        return True

    log("  [aviso] Tesseract não encontrado: 'clicar em X' e ler a tela não vão funcionar.")
    log(f"          Instale em: {PAGINA_DO_TESSERACT} (marque o idioma português)")

    return False


def main() -> int:
    _logar("  Conferindo modelos da câmera (rosto, mãos)...")
    garantir_modelos_da_camera(RAIZ, _logar)

    _logar("  Conferindo o Ollama...")
    garantir_modelo_do_ollama(modelo_do_config(RAIZ), _logar)

    _logar("  Conferindo o Tesseract...")
    avisar_do_tesseract(_logar)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
