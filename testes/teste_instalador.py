"""Testes do instalador (`instalar_jarvis.py`).

O `.bat` não roda fora do Windows, mas as decisões dele sim: aqui o `ollama`,
o `subprocess` e o navegador são dublês, então dá para garantir que a instalação
avisa em vez de estourar em cada caso (sem Ollama, modelo faltando, tudo pronto).

    python -m unittest testes.teste_instalador -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import instalar_jarvis as instalador  # noqa: E402

LISTA_COM_QWEN = """NAME                    ID              SIZE      MODIFIED
qwen2.5:3b              1234567890ab    1.9 GB    2 days ago
nomic-embed-text        abcdef123456    274 MB    4 days ago
"""

LISTA_VAZIA = "NAME    ID    SIZE    MODIFIED\n"


class _ExecucaoFalsa:
    """Finge o `subprocess.run`: anota o comando e devolve uma saída."""

    def __init__(self, saida="", codigo=0):
        self.saida = saida
        self.codigo = codigo
        self.comandos: list[list[str]] = []

    def __call__(self, comando, **extras):
        self.comandos.append(list(comando))

        if comando[-1] == "list":
            return type("R", (), {"returncode": self.codigo, "stdout": self.saida})()

        return type("R", (), {"returncode": self.codigo, "stdout": ""})()


class _NavegadorFalso:
    def __init__(self):
        self.abertos: list[str] = []

    def __call__(self, url):
        self.abertos.append(url)
        return True


class TesteModeloDoConfig(unittest.TestCase):
    def test_sem_config_usa_o_padrao(self):
        with tempfile.TemporaryDirectory() as pasta:
            self.assertEqual(instalador.modelo_do_config(Path(pasta)), instalador.MODELO_PADRAO)

    def test_config_quebrado_usa_o_padrao(self):
        with tempfile.TemporaryDirectory() as pasta:
            (Path(pasta) / "config_jarvis.json").write_text("{isso nao e json", encoding="utf-8")

            self.assertEqual(instalador.modelo_do_config(Path(pasta)), instalador.MODELO_PADRAO)

    def test_config_com_modelo_respeita_a_escolha(self):
        with tempfile.TemporaryDirectory() as pasta:
            (Path(pasta) / "config_jarvis.json").write_text(
                json.dumps({"modelo_local": "qwen3:8b"}), encoding="utf-8"
            )

            self.assertEqual(instalador.modelo_do_config(Path(pasta)), "qwen3:8b")

    def test_modelo_em_branco_usa_o_padrao(self):
        with tempfile.TemporaryDirectory() as pasta:
            (Path(pasta) / "config_jarvis.json").write_text(
                json.dumps({"modelo_local": "   "}), encoding="utf-8"
            )

            self.assertEqual(instalador.modelo_do_config(Path(pasta)), instalador.MODELO_PADRAO)


class TesteInventario(unittest.TestCase):
    def test_le_a_tabela_do_ollama_list(self):
        self.assertEqual(
            instalador.inventario_do_ollama(LISTA_COM_QWEN),
            {"qwen2.5:3b", "nomic-embed-text"},
        )

    def test_saida_vazia_nao_estoura(self):
        self.assertEqual(instalador.inventario_do_ollama(""), set())
        self.assertEqual(instalador.inventario_do_ollama(None), set())

    def test_cabecalho_sozinho_nao_vira_modelo(self):
        self.assertEqual(instalador.inventario_do_ollama(LISTA_VAZIA), set())

    def test_modelo_com_tag_e_encontrado_pela_raiz(self):
        self.assertFalse(instalador.falta_baixar({"qwen2.5:3b"}, "qwen2.5"))
        self.assertFalse(instalador.falta_baixar({"qwen2.5"}, "qwen2.5:3b"))
        self.assertFalse(instalador.falta_baixar({"qwen2.5:3b"}, "qwen2.5:3b"))

    def test_modelo_ausente_precisa_baixar(self):
        self.assertTrue(instalador.falta_baixar({"llama3.1:8b"}, "qwen2.5:3b"))
        self.assertTrue(instalador.falta_baixar(set(), "qwen2.5:3b"))

    def test_modelo_em_branco_nao_pede_download(self):
        self.assertFalse(instalador.falta_baixar({"qwen2.5:3b"}, ""))


class TesteOllama(unittest.TestCase):
    def test_sem_ollama_avisa_abre_a_pagina_e_nao_estoura(self):
        falas: list[str] = []
        navegador = _NavegadorFalso()

        resultado = instalador.garantir_modelo_do_ollama(
            "qwen2.5:3b",
            log=falas.append,
            achar=lambda nome: None,
            abrir=navegador,
        )

        self.assertFalse(resultado)
        self.assertEqual(navegador.abertos, [instalador.PAGINA_DO_OLLAMA])
        self.assertTrue(any("não está instalado" in fala for fala in falas))

    def test_modelo_ja_instalado_nao_baixa_nada(self):
        execucao = _ExecucaoFalsa(saida=LISTA_COM_QWEN)
        falas: list[str] = []

        resultado = instalador.garantir_modelo_do_ollama(
            "qwen2.5:3b",
            log=falas.append,
            achar=lambda nome: "ollama.exe",
            executar=execucao,
            abrir=_NavegadorFalso(),
        )

        self.assertTrue(resultado)
        self.assertEqual([comando[-1] for comando in execucao.comandos], ["list"])
        self.assertTrue(any("já instalado" in fala for fala in falas))

    def test_modelo_faltando_baixa_com_pull(self):
        execucao = _ExecucaoFalsa(saida=LISTA_VAZIA)
        falas: list[str] = []

        instalador.garantir_modelo_do_ollama(
            "qwen3:8b",
            log=falas.append,
            achar=lambda nome: "ollama.exe",
            executar=execucao,
            abrir=_NavegadorFalso(),
        )

        self.assertEqual(
            execucao.comandos,
            [["ollama.exe", "list"], ["ollama.exe", "pull", "qwen3:8b"]],
        )
        self.assertTrue(any("Baixando o modelo" in fala for fala in falas))

    def test_pull_que_falha_avisa_e_nao_derruba_a_instalacao(self):
        execucao = _ExecucaoFalsa(saida=LISTA_VAZIA, codigo=1)
        falas: list[str] = []

        resultado = instalador.garantir_modelo_do_ollama(
            "qwen3:8b",
            log=falas.append,
            achar=lambda nome: "ollama.exe",
            executar=execucao,
            abrir=_NavegadorFalso(),
        )

        self.assertTrue(resultado)  # a instalação segue
        self.assertTrue(any("ollama pull" in fala for fala in falas))

    def test_ollama_que_estoura_no_list_nao_derruba_a_instalacao(self):
        def execucao_quebrada(comando, **extras):
            raise OSError("deu ruim")

        resultado = instalador.garantir_modelo_do_ollama(
            "qwen2.5:3b",
            log=lambda *_: None,
            achar=lambda nome: "ollama.exe",
            executar=execucao_quebrada,
            abrir=_NavegadorFalso(),
        )

        self.assertTrue(resultado)


class TesteTesseract(unittest.TestCase):
    def test_avisar_quando_falta(self):
        falas: list[str] = []

        self.assertFalse(instalador.avisar_do_tesseract(log=falas.append, achar=lambda nome: None))
        self.assertTrue(any("Tesseract não encontrado" in fala for fala in falas))

    def test_sem_aviso_quando_existe(self):
        falas: list[str] = []

        self.assertTrue(
            instalador.avisar_do_tesseract(log=falas.append, achar=lambda nome: "tesseract.exe")
        )
        self.assertTrue(any("Tesseract encontrado" in fala for fala in falas))


class TesteMainDoInstalador(unittest.TestCase):
    def test_main_nunca_estoura_e_devolve_zero(self):
        """Sem Ollama, sem Tesseract, sem internet: ainda assim, código 0."""

        original_ollama = instalador.garantir_modelo_do_ollama
        original_camera = instalador.garantir_modelos_da_camera
        original_tesseract = instalador.avisar_do_tesseract

        instalador.garantir_modelo_do_ollama = lambda *a, **k: False
        instalador.garantir_modelos_da_camera = lambda *a, **k: False
        instalador.avisar_do_tesseract = lambda *a, **k: False
        self.addCleanup(lambda: setattr(instalador, "garantir_modelo_do_ollama", original_ollama))
        self.addCleanup(lambda: setattr(instalador, "garantir_modelos_da_camera", original_camera))
        self.addCleanup(lambda: setattr(instalador, "avisar_do_tesseract", original_tesseract))

        self.assertEqual(instalador.main(), 0)


class TesteArquivosDeInstalacao(unittest.TestCase):
    """Os .bat existem e apontam para os arquivos certos."""

    def test_bats_existem(self):
        for nome in ("instalar.bat", "iniciar.bat"):
            with self.subTest(nome=nome):
                self.assertTrue((RAIZ / nome).is_file(), f"{nome} não existe")

    def test_iniciar_bat_usa_o_python_do_venv(self):
        conteudo = (RAIZ / "iniciar.bat").read_text(encoding="utf-8", errors="replace")

        self.assertIn(r".venv\Scripts\python.exe", conteudo)
        self.assertIn("iniciar_jarvis.py", conteudo)

    def test_instalar_bat_chama_o_instalador_e_o_requirements(self):
        conteudo = (RAIZ / "instalar.bat").read_text(encoding="utf-8", errors="replace")

        self.assertIn("instalar_jarvis.py", conteudo)
        self.assertIn("requirements.txt", conteudo)
        self.assertIn("python.org", conteudo)  # o caminho quando falta o Python
        self.assertIn("iniciar.bat", conteudo)  # diz o que fazer depois


if __name__ == "__main__":
    unittest.main()
