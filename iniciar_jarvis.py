"""Sobe a Jarvis completa: processo de voz + interface, e cuida dos dois.

    python iniciar_jarvis.py

O que mudou em relação à versão anterior
----------------------------------------
* confere o ambiente **antes** de abrir (dependências e modelos da câmera),
  avisando com clareza em vez de deixar a interface cair com traceback;
* vigia os dois processos: se a interface morrer, a voz é encerrada junto —
  antes sobrava um processo falando sozinho, sem janela (e a trava de
  instância única impedia abrir de novo);
* funciona também fora do Windows (antes só existia o caminho do `.venv` do
  Windows, com `.exe`).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQUIVO_LOG = PASTA / "jarvis.log"

CODIGO_DE_TESTE = """
import sys
faltando = []
for modulo in ("mediapipe", "cv2", "PySide6", "sounddevice", "mss", "psutil"):
    try:
        __import__(modulo)
    except Exception as erro:
        faltando.append(f"{modulo}: {erro}")
print("|".join(faltando))
"""


def descobrir_python() -> Path:
    """Escolhe o Python do projeto: o do .venv, se existir."""

    candidatos = []

    if os.name == "nt":
        candidatos += [
            PASTA / ".venv" / "Scripts" / "python.exe",
            PASTA / "venv" / "Scripts" / "python.exe",
        ]
    else:
        candidatos += [
            PASTA / ".venv" / "bin" / "python3",
            PASTA / ".venv" / "bin" / "python",
            PASTA / "venv" / "bin" / "python3",
        ]

    for candidato in candidatos:
        if candidato.exists():
            return candidato

    return Path(sys.executable)


def conferir_ambiente(python: Path) -> list[str]:
    """Devolve a lista de problemas encontrados (vazia = tudo certo)."""

    problemas = []

    try:
        resultado = subprocess.run(
            [str(python), "-c", CODIGO_DE_TESTE],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            cwd=str(PASTA),
        )
    except Exception as erro:
        return [f"Não consegui executar {python}: {erro}"]

    if resultado.returncode != 0:
        return [
            f"{python} não rodou o teste de dependências:\n"
            + (resultado.stderr or "").strip()[-800:]
        ]

    if (resultado.stdout or "").strip():
        problemas.append("Dependências faltando:\n  - " + "\n  - ".join(resultado.stdout.strip().split("|")))

    try:
        from preparar_modelos import garantir_modelos

        faltando = garantir_modelos(PASTA, baixar=False, log=lambda _: None)
        if faltando:
            problemas.append(
                "Modelos da câmera faltando: "
                + ", ".join(faltando)
                + "\n  (rode: python preparar_modelos.py — o resto do Jarvis funciona sem eles)"
            )
    except Exception as erro:
        problemas.append(f"Não consegui conferir os modelos: {erro}")

    return problemas


def encerrar(processo: subprocess.Popen | None, nome: str) -> None:
    if processo is None or processo.poll() is not None:
        return

    print(f"Encerrando {nome}...")
    processo.terminate()

    try:
        processo.wait(timeout=8)
    except subprocess.TimeoutExpired:
        print(f"{nome} não respondeu; forçando.")
        processo.kill()


def ultimas_linhas_do_log(quantidade: int = 15) -> str:
    try:
        linhas = ARQUIVO_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(linhas[-quantidade:])
    except OSError:
        return ""


def main() -> int:
    python = descobrir_python()
    print(f"Python usado: {python}")

    problemas = conferir_ambiente(python)

    for problema in problemas:
        print(f"\n[aviso] {problema}")

    if problemas:
        print()

    cria_janela_de_console = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)

    processo_voz = subprocess.Popen(
        [str(python), "main.py"],
        cwd=str(PASTA),
        creationflags=cria_janela_de_console,
    )
    processo_tela = subprocess.Popen(
        [str(python), "jarvis_tela.py"],
        cwd=str(PASTA),
    )

    print("Jarvis iniciada. Feche a interface pela bandeja (Fechar Jarvis) ou feche esta janela.")

    try:
        while True:
            time.sleep(0.5)

            codigo_tela = processo_tela.poll()

            if codigo_tela is not None:
                print(f"\nA interface encerrou (código {codigo_tela}).")
                if codigo_tela != 0:
                    print("Últimas linhas do log:")
                    print(ultimas_linhas_do_log())
                return codigo_tela

            codigo_voz = processo_voz.poll()

            if codigo_voz is not None:
                print(
                    f"\nO processo de voz encerrou (código {codigo_voz}). "
                    "A interface continua aberta; feche pela bandeja."
                )
                if codigo_voz != 0:
                    print("Últimas linhas do log:")
                    print(ultimas_linhas_do_log())

                processo_voz = None

    except KeyboardInterrupt:
        print("\nEncerrando a Jarvis...")
        return 0
    finally:
        # Garante que nenhum dos dois fique rodando sozinho (era o caso
        # quando a interface morria e a voz continuava falando).
        encerrar(processo_voz, "processo de voz")
        encerrar(processo_tela, "interface")


if __name__ == "__main__":
    sys.exit(main())
