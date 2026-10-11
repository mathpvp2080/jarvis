"""Compara o detector de voz antigo (de `main.py` original) com o novo.

Uso:
    python testes/comparar_antigo_novo.py

Serve como documentação viva da correção do bug B8: mostra, para os mesmos
sinais, quantas frases cada algoritmo captura. Rode sem argumentos.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis_audio import DetectorDeVoz  # noqa: E402
from testes.teste_audio import fala, ruido, tom  # noqa: E402

# Parâmetros originais do main.py (antes)
LIMIAR_MINIMO_DE_VOZ = 120
MULTIPLICADOR_DE_RUIDO = 1.8
SILENCIO_PARA_ENCERRAR = 1.1
TEMPO_MAXIMO_DE_FALA = 20
BLOCOSIZE = 1024


def detector_antigo(blocos: list[np.ndarray]) -> int:
    """Reimplementação fiel da lógica de `ouvir_ate_silencio` de main.py.

    Repare no cálculo do piso: `min(nr*1.08 + nivel*0.12, max(nivel, nr))`,
    que só consegue subir e unicamente nos primeiros 1,0 s de cada escuta.
    """

    nivel_ruido = float(LIMIAR_MINIMO_DE_VOZ / MULTIPLICADOR_DE_RUIDO)
    inicio_espera = 0.0
    blocos_anteriores: list[np.ndarray] = []
    blocos_gravados: list[np.ndarray] = []
    iniciou_fala = False
    inicio_fala = 0.0
    ultimo_bloco_com_voz = 0.0
    blocos_com_voz = 0
    frases = 0

    for indice, bloco in enumerate(blocos):
        agora = indice * BLOCOSIZE / 16000
        amostras = bloco.astype(np.float32).reshape(-1)
        nivel = float(np.sqrt(np.mean(amostras * amostras)))

        if not iniciou_fala:
            blocos_anteriores.append(bloco)
            blocos_anteriores = blocos_anteriores[-8:]

            if agora - inicio_espera <= 1.0:
                nivel_ruido = min(
                    nivel_ruido * 1.08 + nivel * 0.12,
                    max(nivel, nivel_ruido),
                )

            limiar = max(LIMIAR_MINIMO_DE_VOZ, nivel_ruido * MULTIPLICADOR_DE_RUIDO)

            if nivel > limiar:
                blocos_com_voz += 1
            else:
                blocos_com_voz = 0

            if blocos_com_voz >= 2:
                iniciou_fala = True
                inicio_fala = agora
                ultimo_bloco_com_voz = agora
                blocos_gravados.extend(blocos_anteriores)
                blocos_anteriores = []

            continue

        blocos_gravados.append(bloco)
        limiar = max(LIMIAR_MINIMO_DE_VOZ, nivel_ruido * MULTIPLICADOR_DE_RUIDO)

        if nivel > limiar:
            ultimo_bloco_com_voz = agora

        terminou = (
            agora - ultimo_bloco_com_voz >= SILENCIO_PARA_ENCERRAR
            and agora - inicio_fala >= 0.45
        ) or agora - inicio_fala >= TEMPO_MAXIMO_DE_FALA

        if terminou:
            if blocos_gravados:
                frases += 1
            iniciou_fala = False
            blocos_gravados = []
            blocos_com_voz = 0

    return frases


CENARIOS = {
    "Sala silenciosa + fala": lambda: (
        ruido(25, blocos=15) + fala(700, blocos=23) + ruido(25, blocos=25)
    ),
    "Teclado e clique de mouse (sem fala)": lambda: (
        sum(
            [
                (ruido(400, blocos=1, semente=i) if i % 7 == 0 else ruido(25, blocos=1, semente=i))
                for i in range(160)
            ],
            [],
        )
    ),
    "Ventilador forte (RMS 600) + fala": lambda: (
        tom(120, 600, blocos=30) + fala(900, blocos=25) + tom(120, 600, blocos=25)
    ),
    "Ventilador muito forte (RMS 2000) + fala": lambda: (
        tom(120, 2000, blocos=30) + fala(1100, blocos=25) + tom(120, 2000, blocos=25)
    ),
}


def main() -> None:
    print(f"{'cenário':<44} {'antigo':>7} {'novo':>6}  situação")
    print("-" * 80)

    for nome, gerar in CENARIOS.items():
        blocos = gerar()

        antigo = detector_antigo(blocos)

        detector = DetectorDeVoz()
        novo = 0
        for bloco in blocos:
            if detector.processar(bloco) is not None:
                novo += 1

        if "sem fala" in nome:
            situacao = "ok" if novo == 0 else "FALSO POSITIVO"
        else:
            esperado = 1
            if antigo == esperado and novo == esperado:
                situacao = "ok"
            elif novo == esperado:
                situacao = "corrigido pelo novo"
            elif novo < esperado:
                situacao = "não capturou"
            else:
                situacao = "capturou demais"

        print(f"{nome:<44} {antigo:>7} {novo:>6}  {situacao}")

    print(
        "\nLegenda: 'antigo' = lógica original de main.py; 'novo' = jarvis_audio.DetectorDeVoz.\n"
        "Cada cenário de fala deveria render 1 frase; o de teclado, 0."
    )


if __name__ == "__main__":
    main()
