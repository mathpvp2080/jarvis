"""Provisiona os modelos que a câmera do Jarvis precisa.

Rode uma vez (ou deixe a interface rodar sozinha ao abrir):

    python preparar_modelos.py

O problema que isto resolve
---------------------------
`jarvis_tela.py` exige três arquivos de modelo que **não estão no repositório**
e que nada baixava: num clone novo (ou em outro PC), a interface morria no
startup com um traceback de mediapipe.

Dois deles podem ser obtidos **sem internet**: o pacote `mediapipe` já traz o
modelo de detecção de rosto (com outro nome). Os outros dois são bundles
`.task` que só existem para download — o script baixa, confere o tamanho e
avisa com clareza se não conseguir.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

PASTA = Path(__file__).resolve().parent

# Onde o mediapipe guarda o mesmo modelo de rosto que o app usa, com outro nome.
NOME_DO_MODELO_DE_ROSTO = "blaze_face_short_range.tflite"
NOME_INTERNO_NO_MEDIAPIPE = ("modules", "face_detection", "face_detection_short_range.tflite")

MODELOS_PARA_BAIXAR = {
    "face_landmarker.task": {
        "url": (
            "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
            "face_landmarker/float16/1/face_landmarker.task"
        ),
        "tamanho_minimo": 2_000_000,
        "para_que": "exoesqueleto facial e estimativa de expressões",
    },
    "hand_landmarker.task": {
        "url": (
            "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
            "hand_landmarker/float16/1/hand_landmarker.task"
        ),
        "tamanho_minimo": 5_000_000,
        "para_que": "detecção de mãos, gestos e controle do holograma",
    },
}


def _logar(mensagem: str) -> None:
    print(mensagem)


def copiar_modelo_de_rosto(pasta: Path = PASTA, log=_logar) -> bool:
    """Copia o modelo de rosto do pacote mediapipe (funciona sem internet)."""

    destino = Path(pasta) / NOME_DO_MODELO_DE_ROSTO

    if destino.exists() and destino.stat().st_size > 50_000:
        log(f"[ok] {destino.name} já existe ({destino.stat().st_size / 1024:.0f} KB)")
        return True

    try:
        import mediapipe
    except Exception as erro:
        log(f"[--] mediapipe não instalado ({erro}); instale com: pip install mediapipe")
        return False

    origem = Path(mediapipe.__file__).resolve().parent.joinpath(*NOME_INTERNO_NO_MEDIAPIPE)

    if not origem.exists():
        log(f"[--] modelo de rosto não encontrado dentro do mediapipe ({origem})")
        return False

    try:
        shutil.copyfile(origem, destino)
    except OSError as erro:
        log(f"[--] não consegui copiar o modelo de rosto: {erro}")
        return False

    log(f"[ok] {destino.name} preparado a partir do mediapipe ({destino.stat().st_size / 1024:.0f} KB)")
    return True


def baixar_modelo(nome: str, informacoes: dict, pasta: Path = PASTA, log=_logar) -> bool:
    """Baixa um bundle `.task` conferindo o tamanho antes de aceitar."""

    from urllib.request import Request, urlopen

    destino = Path(pasta) / nome

    if destino.exists() and destino.stat().st_size >= informacoes["tamanho_minimo"]:
        log(f"[ok] {nome} já existe ({destino.stat().st_size / 1024 / 1024:.1f} MB)")
        return True

    log(f"[..] baixando {nome} ({informacoes['para_que']})...")
    temporario = Path(tempfile.gettempdir()) / f"{nome}.parcial"

    try:
        requisicao = Request(informacoes["url"], headers={"User-Agent": "jarvis/1.0"})

        with urlopen(requisicao, timeout=90) as resposta, open(temporario, "wb") as arquivo:
            total = 0
            while True:
                pedaco = resposta.read(256 * 1024)
                if not pedaco:
                    break
                arquivo.write(pedaco)
                total += len(pedaco)

        if total < informacoes["tamanho_minimo"]:
            raise ValueError(f"arquivo baixado é pequeno demais ({total / 1024:.0f} KB)")

        os.replace(temporario, destino)
        log(f"[ok] {nome} baixado ({total / 1024 / 1024:.1f} MB)")
        return True

    except Exception as erro:
        try:
            temporario.unlink(missing_ok=True)
        except OSError:
            pass

        log(f"[--] não consegui baixar {nome}: {type(erro).__name__}: {erro}")
        log(f"     baixe manualmente em: {informacoes['url']}")
        log(f"     e salve como: {destino}")
        return False


def garantir_modelos(pasta: Path = PASTA, baixar: bool = True, log=_logar) -> list[str]:
    """Garante os três modelos e devolve a lista dos que continuam faltando."""

    pasta = Path(pasta)
    faltando = []

    if not copiar_modelo_de_rosto(pasta, log):
        faltando.append(NOME_DO_MODELO_DE_ROSTO)

    for nome, informacoes in MODELOS_PARA_BAIXAR.items():
        caminho = pasta / nome
        if caminho.exists() and caminho.stat().st_size >= informacoes["tamanho_minimo"]:
            log(f"[ok] {nome} já existe ({caminho.stat().st_size / 1024 / 1024:.1f} MB)")
            continue

        if not baixar:
            log(f"[--] {nome} faltando ({informacoes['para_que']})")
            faltando.append(nome)
            continue

        if not baixar_modelo(nome, informacoes, pasta, log):
            faltando.append(nome)

    return faltando


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    print(f"Pasta do projeto: {PASTA}\n")

    faltando = garantir_modelos()

    print()
    if faltando:
        print("Faltando: " + ", ".join(faltando))
        print("Sem esses arquivos, os recursos de câmera correspondentes não funcionam")
        print("(o restante da Jarvis continua funcionando normalmente).")
        return 1

    print("Todos os modelos estão no lugar: a câmera pode ser usada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
