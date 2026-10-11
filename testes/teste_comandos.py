"""Testes de integração das rotas de comando do main.py.

O `main.py` é importado de verdade — só as peças que dependem de Windows,
câmera, tesseract e interface gráfica são substituídas por dublês. Assim dá
para garantir que nenhum comando real do histórico explode, e que o roteamento
continua ligado nos lugares certos.

    python -m unittest discover -s testes -t . -v
"""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))


# ---------------------------------------------------------------------------
# Dublês das dependências pesadas (instaladas só onde roda de verdade)
# ---------------------------------------------------------------------------


class _ModuloFalso(types.ModuleType):
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


def _instalar_dubles():
    for nome in ("mss", "pyautogui", "pytesseract", "sounddevice",
                 "speech_recognition", "PIL", "PIL.Image"):
        if nome not in sys.modules:
            try:
                __import__(nome)
            except Exception:
                sys.modules[nome] = _ModuloFalso(nome)

    if "pyperclip" not in sys.modules:
        sys.modules["pyperclip"] = _AreaDeTransferenciaFalsa()


_instalar_dubles()

import main  # noqa: E402  (precisa vir depois dos dublês)


class TesteRotasDeComando(unittest.TestCase):
    """Chama `processar_comando` e observa o que ele decide fazer."""

    def setUp(self):
        self.falas: list[str] = []
        self.eventos: list[tuple] = []
        self.consultas: list[str] = []
        self.pastas = tempfile.TemporaryDirectory()
        self.addCleanup(self.pastas.cleanup)

        pasta = Path(self.pastas.name)

        # Isola memória, comandos aprendidos e canais da interface.
        self._originais = {
            "ARQUIVO_MEMORIA": main.ARQUIVO_MEMORIA,
            "ARQUIVO_COMANDOS_APRENDIDOS": main.ARQUIVO_COMANDOS_APRENDIDOS,
            "memoria": main.memoria,
            "comandos_aprendidos": main.comandos_aprendidos,
            "informar_interface": main.informar_interface,
            "falar": main.falar,
            "responder_com_ollama": main.responder_com_ollama,
            "conteudo_falar": main.falar,
        }

        main.ARQUIVO_MEMORIA = pasta / "memoria.json"
        main.ARQUIVO_COMANDOS_APRENDIDOS = pasta / "aprendidos.json"
        main.memoria = []
        main.comandos_aprendidos = {}

        main.falar = self.falas.append
        main.responder_com_ollama = self.consultas.append
        main.informar_interface = lambda tipo, dados=None: self.eventos.append((tipo, dados))

        self.addCleanup(self._restaurar)

    def _restaurar(self):
        main.ARQUIVO_MEMORIA = self._originais["ARQUIVO_MEMORIA"]
        main.ARQUIVO_COMANDOS_APRENDIDOS = self._originais["ARQUIVO_COMANDOS_APRENDIDOS"]
        main.memoria = self._originais["memoria"]
        main.comandos_aprendidos = self._originais["comandos_aprendidos"]
        main.falar = self._originais["falar"]
        main.responder_com_ollama = self._originais["responder_com_ollama"]
        main.informar_interface = self._originais["informar_interface"]

    # -- testes ------------------------------------------------------------

    def test_pergunta_de_hora_responde_sem_consultar_a_ia(self):
        main.processar_comando("que horas são?")

        self.assertTrue(self.falas, "deveria ter respondido")
        self.assertIn("Agora são", self.falas[0])
        self.assertEqual(self.consultas, [], "não precisa consultar a IA")

    def test_sair_encerra_o_programa(self):
        continuar = main.processar_comando("sair")

        self.assertFalse(continuar)
        self.assertIn("Até logo!", self.falas)

    def test_abrir_youtube_music_manda_o_painel_navegar(self):
        main.processar_comando("abrir youtube music")

        pedidos = [dados.get("url", "") for tipo, dados in self.eventos if tipo == "navegar_navegador"]
        self.assertTrue(
            any("music.youtube.com" in url for url in pedidos),
            f"o painel deveria receber a URL; recebeu {pedidos}",
        )

    def test_comando_aprendido_executa(self):
        main.processar_comando("quando eu disser xablau faca que horas sao")

        self.assertIn("xablau", main.comandos_aprendidos)

        self.falas.clear()
        main.processar_comando("xablau")

        self.assertTrue(
            any("Agora são" in fala for fala in self.falas),
            f"o comando aprendido deveria ser executado; falas: {self.falas}",
        )

    def test_comandos_aprendidos_em_ciclo_nao_estouram(self):
        """Regressão do bug B18: A -> B -> A causava RecursionError."""

        main.comandos_aprendidos = {"um": "dois", "dois": "um"}

        continuar = main.processar_comando("um")

        self.assertTrue(continuar)
        self.assertTrue(
            any("ciclo" in fala for fala in self.falas),
            f"deveria avisar sobre o ciclo; falas: {self.falas}",
        )

    def test_conversa_livre_vai_para_a_ia_local(self):
        main.processar_comando("me conta uma piada")

        self.assertEqual(self.consultas, ["me conta uma piada"])

    def test_pesquisa_usa_o_navegador_do_painel(self):
        main.processar_comando("pesquisar guns n roses no youtube")

        urls = [d.get("url", "") for t, d in self.eventos if t == "navegar_navegador"]
        self.assertTrue(any("google.com/search" in url for url in urls), urls)

    def test_comandos_reais_do_historico_nao_estouram(self):
        """Nenhum comando que o usuário já tentou pode virar exceção."""

        comandos = [
            "abrir google chrome",
            "jefferson",
            "jarvis",
            "ta vendo minha tela",
            "o que estou vendo",
            "abrir chat gpt",
            "corrigir e colar",
            "tocar back in black no youtube music",
            "abrir steam",
            "abra o chrome",
            "descreva o que esta na minha tela",
            "quero um holograma",
            "tira holograma para mim",
            "desativa o holograma",
            "esconder painel",
            "aumentar volume",
            "proxima musica",
            "silenciar",
            "ler minha tela",
            "clicar em confirmar",
            "me conta uma piada",
            "",
            "   ",
        ]

        for comando in comandos:
            with self.subTest(comando=comando):
                try:
                    main.processar_comando(comando)
                except Exception as erro:  # noqa: BLE001
                    self.fail(f"comando {comando!r} levantou {type(erro).__name__}: {erro}")

    def test_memoria_registra_o_comando(self):
        main.processar_comando("que horas são?")

        registros = [item for item in main.memoria if item.get("tipo") == "Usuário"]
        self.assertEqual(len(registros), 1)
        self.assertEqual(registros[0]["texto"], "que horas são?")


class TesteExtracaoDaPalavraJarvis(unittest.TestCase):
    def test_reconhece_ativacao_no_meio_da_frase(self):
        self.assertEqual(
            main.extrair_comando_apos_ativacao("oi jarvis tocar acdc"), "tocar acdc"
        )

    def test_ativacao_sozinha_devolve_texto_vazio(self):
        self.assertEqual(main.extrair_comando_apos_ativacao("jarvis"), "")

    def test_frase_sem_ativacao_e_ignorada(self):
        self.assertIsNone(main.extrair_comando_apos_ativacao("ligar a luz da sala"))

    def test_variacoes_comuns_do_nome(self):
        for variacao in ("jarves", "jarviz", "javis", "javi"):
            self.assertEqual(
                main.extrair_comando_apos_ativacao(f"{variacao} que horas sao"),
                "que horas sao",
                variacao,
            )


class TesteNormalizacao(unittest.TestCase):
    def test_remove_acentos_e_pontuacao(self):
        self.assertEqual(main.normalizar("Olá, Járvis! Tô aqui?"), "ola jarvis to aqui")

    def test_palavras_importantes_ignora_palavras_vazias(self):
        importantes = main.palavras_importantes("qual é a capital do Brasil")
        self.assertIn("capital", importantes)
        self.assertIn("brasil", importantes)
        self.assertNotIn("qual", importantes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
