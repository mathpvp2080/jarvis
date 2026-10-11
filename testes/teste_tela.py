"""Fumaça da interface (jarvis_tela.py) sem abrir janela de verdade.

O que isto pega: erro de nome/atributo na montagem da interface — o tipo de
falha que só apareceria quando você abrisse o programa no Windows.

Como funciona: o QtWebEngine (navegador embutido) é substituído por dublês,
a plataforma do Qt vira "offscreen" e o laço de eventos não roda. Se a
interface for montada inteira sem estourar, é porque os nomes todos existem.

Roda em qualquer sistema; sem PySide6, o teste é pulado.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
import unittest
from pathlib import Path

PASTA = Path(__file__).resolve().parent.parent

try:
    import PySide6
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication, QWidget

    # Referências usadas no teste (também acalmam o pyflakes).
    _FERRAMENTAS = (PySide6.__version__, QUrl, QApplication, QWidget)

    TEM_PYSIDE = True
except Exception:  # pragma: no cover - só roda sem PySide6 instalado
    TEM_PYSIDE = False


if TEM_PYSIDE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    class _FalsoUniversal:
        """Objeto que responde a qualquer chamada, devolvendo outro igual.

        Serve para `page()`, `titleChanged.connect(...)`, `setZoomFactor(...)`
        — o que a interface usa do navegador — sem implementar nada de verdade.
        """

        def __init__(self, *args, **kwargs):
            pass

        def __getattr__(self, nome):
            if nome.startswith("__"):
                raise AttributeError(nome)
            return self

        def __call__(self, *args, **kwargs):
            return _FalsoUniversal()

    class _NavegadorFalso(QWidget):
        """Dublê do QWebEngineView: é um widget de verdade, mas vazio."""

        def __init__(self, *args, **kwargs):
            super().__init__()

        def __getattr__(self, nome):
            if nome.startswith("__"):
                raise AttributeError(nome)
            return _FalsoUniversal()

    modulo_widgets = types.ModuleType("PySide6.QtWebEngineWidgets")
    modulo_widgets.QWebEngineView = _NavegadorFalso

    modulo_core = types.ModuleType("PySide6.QtWebEngineCore")
    modulo_core.QWebEnginePage = QWidget
    modulo_core.QWebEngineProfile = type(
        "QWebEngineProfile", (), {"defaultProfile": staticmethod(lambda: _FalsoUniversal())}
    )

    sys.modules.setdefault("PySide6.QtWebEngineWidgets", modulo_widgets)
    sys.modules.setdefault("PySide6.QtWebEngineCore", modulo_core)


@unittest.skipUnless(TEM_PYSIDE, "PySide6 não instalado")
class TesteFumacaDaInterface(unittest.TestCase):
    def test_interface_monta_sem_estourar(self):
        # Quem cria a QApplication (e a janela) é o próprio módulo — criar uma
        # aqui daria "A QApplication instance already exists" no Windows.
        executou = {"vezes": 0}

        def exec_falso(self, *args, **kwargs):
            executou["vezes"] += 1
            return 0

        original = QApplication.exec
        QApplication.exec = exec_falso
        modulo = None

        try:
            sys.path.insert(0, str(PASTA))

            if "jarvis_tela" in sys.modules:
                del sys.modules["jarvis_tela"]

            try:
                modulo = importlib.import_module("jarvis_tela")
            except SystemExit:
                modulo = sys.modules.get("jarvis_tela")  # termina com sys.exit(app.exec())

        finally:
            QApplication.exec = original

        self.assertIsNotNone(modulo, "o módulo da interface deveria ter sido importado")
        self.assertTrue(hasattr(modulo, "JanelaJarvis"))
        self.assertTrue(hasattr(modulo, "CODIGOS_DE_TECLA"))
        self.assertIsNotNone(modulo.janela)
        self.assertTrue(hasattr(modulo.janela, "enviar_tecla_windows"))
        self.assertEqual(executou["vezes"], 1, "a janela deveria ter sido montada uma vez")


if __name__ == "__main__":
    unittest.main()
