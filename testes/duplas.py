"""Dublês das dependências que só existem na máquina de verdade.

O `main.py` importa pyautogui, mss, pytesseract, som e reconhecimento de voz no
topo do arquivo. Sem eles instalados, `import main` falha — e nenhum teste roda
em Linux. Aqui esses módulos são substituídos por objetos que engolem tudo e
devolvem outros objetos do mesmo tipo, o suficiente para importar e para
exercitar o roteamento de comandos.

O `pyperclip` é um dublê com estado (guarda o texto), porque o fluxo de correção
de texto lê o que foi copiado.

Usar em qualquer teste que precise do `main`:

    from duplas import instalar_dubles
    instalar_dubles()
    import main
"""

from __future__ import annotations

import sys
import types


class _ModuloFalso(types.ModuleType):
    """Módulo que aceita qualquer acesso, chamada ou atributo."""

    def __getattr__(self, nome):
        if nome.startswith("__"):
            raise AttributeError(nome)
        return _ModuloFalso(f"{self.__name__}.{nome}")

    def __call__(self, *args, **kwargs):
        return _ModuloFalso(f"{self.__name__}()")


class _AreaDeTransferenciaFalsa(types.ModuleType):
    """Dublê com estado, para testar o fluxo de correção de texto."""

    def __init__(self):
        super().__init__("pyperclip")
        self.texto = ""

    def paste(self):
        return self.texto

    def copy(self, texto):
        self.texto = texto


DUPLAS_SIMPLES = (
    "mss",
    "pyautogui",
    "pytesseract",
    "sounddevice",
    "speech_recognition",
    "PIL",
    "PIL.Image",
)


def instalar_dubles():
    """Instala o que faltar — nunca substitui o módulo de verdade."""

    for nome in DUPLAS_SIMPLES:
        if nome in sys.modules:
            continue

        try:
            __import__(nome)
        except Exception:
            sys.modules[nome] = _ModuloFalso(nome)

    if "pyperclip" not in sys.modules:
        sys.modules["pyperclip"] = _AreaDeTransferenciaFalsa()

    return sys.modules["pyperclip"]
