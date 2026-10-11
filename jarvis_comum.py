"""Utilitários compartilhados entre o processo de voz (main.py) e a interface (jarvis_tela.py).

Concentra três coisas que antes existiam duplicadas e frágeis em cada arquivo:

1. Log em arquivo (``jarvis.log``) + captura de exceções não tratadas.
2. Leitura/escrita de JSON à prova de corrupção (escrita atômica).
3. Uma fila de mensagens baseada em arquivos, sem *read-modify-write* — o que
   elimina a perda de comandos/eventos entre os dois processos.

Nada aqui depende de Windows: os módulos que precisam de Windows tratam isso
localmente.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import threading
import time
import uuid
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQUIVO_LOG = PASTA / "jarvis.log"

# Pasta onde cada processo deposita mensagens para o outro.
PASTA_FILA_COMANDOS = PASTA / "fila_comandos"
PASTA_FILA_EVENTOS = PASTA / "fila_eventos"

_LOG_CONFIGURADO = False
_LOCK_JSON = threading.RLock()


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------


def configurar_log(nome: str = "jarvis", nivel: int = logging.INFO) -> logging.Logger:
    """Configura (uma única vez por processo) log em arquivo rotativo + console.

    O arquivo fica na pasta do projeto e é limitado a 2 MB x 3 arquivos, para
    não crescer sem controle.
    """

    global _LOG_CONFIGURADO

    logger = logging.getLogger(nome)

    if _LOG_CONFIGURADO:
        return logger

    logger.setLevel(nivel)
    formato = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(threadName)s: %(message)s",
        datefmt="%d/%m/%Y %H:%M:%S",
    )

    try:
        manipulador = logging.handlers.RotatingFileHandler(
            ARQUIVO_LOG,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        manipulador.setFormatter(formato)
        logger.addHandler(manipulador)
    except OSError:
        # Se não der para escrever o log, o console ainda serve.
        pass

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formato)
    logger.addHandler(console)

    _LOG_CONFIGURADO = True
    logger.info("Log iniciado em %s", ARQUIVO_LOG)
    return logger


def registrar_excecoes_nao_tratadas(logger: logging.Logger, ao_avisar=None) -> None:
    """Instala hooks para que nenhum erro morra invisível.

    ``ao_avisar(mensagem)`` é opcional: na interface, por exemplo, serve para
    mostrar uma notificação na bandeja. Sem isso, um erro dentro de um slot do
    Qt só apareceria no console — que ninguém vê quando o app é aberto por atalho.
    """

    def tratar(tipo, valor, rastro):
        if issubclass(tipo, KeyboardInterrupt):
            return

        logger.critical(
            "Erro não tratado: %s: %s",
            tipo.__name__,
            valor,
            exc_info=(tipo, valor, rastro),
        )

        if ao_avisar is not None:
            try:
                ao_avisar(f"{tipo.__name__}: {valor}")
            except Exception:
                logger.exception("Falha ao avisar sobre o erro")

    sys.excepthook = tratar

    if hasattr(threading, "excepthook"):

        def tratar_thread(args):
            if args.exc_type is SystemExit:
                return
            tratar(args.exc_type, args.exc_value, args.exc_traceback)

        threading.excepthook = tratar_thread


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def ler_json(caminho: Path, padrao):
    """Lê um JSON devolvendo ``padrao`` em qualquer situação de falha."""

    try:
        if not caminho.exists():
            return padrao

        with _LOCK_JSON:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return padrao

    return dados


def escrever_json_atomico(caminho: Path, dados) -> bool:
    """Grava JSON de forma atômica: escreve num temporário e troca o arquivo.

    Assim um leitor nunca encontra um arquivo pela metade (era a causa de
    "Ainda não há mensagens" piscando no painel e de registros perdidos).
    """

    caminho = Path(caminho)
    temporario = caminho.with_name(f"{caminho.name}.{os.getpid()}.tmp")

    try:
        with _LOCK_JSON:
            temporario.write_text(
                json.dumps(dados, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporario, caminho)
        return True
    except OSError:
        try:
            temporario.unlink(missing_ok=True)
        except OSError:
            pass
        return False


# ---------------------------------------------------------------------------
# Fila baseada em arquivos (sem corrida entre processos)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Histórico em uma linha por registro (JSONL)
# ---------------------------------------------------------------------------
#
# O histórico da conversa era uma lista JSON reescrita por inteiro a cada
# mensagem — com milhares de registros, isso fica lento e passa pela janela
# em que o arquivo está truncado (a interface lia e via "sem mensagens").
# Aqui cada mensagem vira uma linha anexada, com rotação por tamanho.

TAMANHO_MAXIMO_DO_HISTORICO = 8 * 1024 * 1024


def ler_jsonl(caminho: Path, limite: int | None = None) -> list:
    """Lê um arquivo de uma linha por registro, ignorando linhas quebradas."""

    registros = []

    try:
        with open(caminho, "r", encoding="utf-8", errors="replace") as arquivo:
            for linha in arquivo:
                linha = linha.strip()

                if not linha:
                    continue

                try:
                    registros.append(json.loads(linha))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return []
    except OSError:
        return []

    if limite:
        return registros[-limite:]

    return registros


def anexar_jsonl(caminho: Path, registro: dict, tamanho_maximo: int = TAMANHO_MAXIMO_DO_HISTORICO) -> bool:
    """Anexa um registro, trocando o arquivo de lugar quando fica grande."""

    caminho = Path(caminho)

    try:
        if caminho.exists() and caminho.stat().st_size >= tamanho_maximo:
            os.replace(caminho, caminho.with_suffix(caminho.suffix + ".1"))

        with open(caminho, "a", encoding="utf-8") as arquivo:
            arquivo.write(json.dumps(registro, ensure_ascii=False) + "\n")

        return True
    except OSError:
        return False


class FilaDeArquivos:
    """Fila de mensagens simples e segura entre processos.

    Em vez de dois processos reescreverem o mesmo JSON (o que perdia mensagens
    quando um lia e apagava enquanto o outro anexava), cada mensagem vira um
    arquivo próprio dentro de uma pasta. Publicar é atômico; consumir é ler e
    remover. Uma mensagem publicada exatamente durante a leitura é pega no
    ciclo seguinte — nunca some.
    """

    def __init__(self, pasta: Path):
        self.pasta = Path(pasta)
        try:
            self.pasta.mkdir(exist_ok=True)
        except OSError:
            pass

    def publicar(self, item) -> bool:
        nome = f"{time.time_ns()}-{os.getpid()}-{uuid.uuid4().hex[:6]}.json"
        return escrever_json_atomico(self.pasta / nome, item)

    def consumir(self, limite: int = 100) -> list:
        if not self.pasta.exists():
            return []

        itens = []
        try:
            arquivos = sorted(self.pasta.glob("*.json"))[:limite]
        except OSError:
            return []

        for arquivo in arquivos:
            dados = ler_json(arquivo, None)

            if dados is None:
                # Arquivo ilegível: guarda com outra extensão em vez de
                # ficar tentando para sempre.
                try:
                    arquivo.rename(arquivo.with_suffix(".erro"))
                except OSError:
                    pass
                continue

            try:
                arquivo.unlink()
            except OSError:
                # Se não conseguiu remover, devolve mesmo assim; o próximo
                # ciclo tratará de novo (evita perder a mensagem).
                pass

            itens.append(dados)

        return itens


FILA_COMANDOS = FilaDeArquivos(PASTA_FILA_COMANDOS)
FILA_EVENTOS = FilaDeArquivos(PASTA_FILA_EVENTOS)


def publicar_comando(texto: str) -> bool:
    """Interface -> voz: pede a execução de um comando digitado/acionado."""

    return FILA_COMANDOS.publicar({"texto": texto, "data": time.time()})


def publicar_evento(tipo: str, dados=None) -> bool:
    """Voz -> interface: pede algo na tela (navegar, avisar, mostrar estado)."""

    return FILA_EVENTOS.publicar(
        {"tipo": tipo, "dados": dados or {}, "data": time.time()}
    )


def consumir_comandos(limite: int = 100) -> list:
    return FILA_COMANDOS.consumir(limite)


def consumir_eventos(limite: int = 100) -> list:
    return FILA_EVENTOS.consumir(limite)
