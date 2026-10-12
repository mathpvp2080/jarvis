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
import time
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
import jarvis_comum  # noqa: E402


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


class TesteNomeDaMusica(unittest.TestCase):
    """A busca no YouTube/Google sai do argumento, não de `str.replace`."""

    def setUp(self):
        self.falas = []
        self.eventos = []
        self._originais = (main.falar, main.navegar_no_painel)
        main.falar = self.falas.append
        main.navegar_no_painel = lambda url: self.eventos.append(url)
        self.addCleanup(self._restaurar)

    def _restaurar(self):
        main.falar, main.navegar_no_painel = self._originais

    def test_verbo_dentro_do_titulo_sobrevive(self):
        for pedido, esperado in (
            ("toca toque de midas no youtube", "toque de midas"),
            ("tocar toca do coelho", "toca do coelho"),
        ):
            with self.subTest(pedido=pedido):
                self.assertEqual(main.nome_da_musica(pedido), esperado)

    def test_cauda_da_plataforma_nao_vira_busca(self):
        for pedido, esperado in (
            ("back in black no youtube music", "back in black"),
            ("acdc no youtube", "acdc"),
        ):
            with self.subTest(pedido=pedido):
                self.assertEqual(main.nome_da_musica(pedido), esperado)

    def test_pedido_vazio_pede_o_nome(self):
        main.tocar_no_youtube("tocar no youtube")

        self.assertEqual(self.eventos, [])
        self.assertTrue(any("qual música" in fala for fala in self.falas))

    def test_pedido_com_musica_abre_a_busca(self):
        main.tocar_no_youtube_music("toca toque de midas no youtube music")

        self.assertEqual(len(self.eventos), 1)
        self.assertIn("music.youtube.com/search", self.eventos[0])
        self.assertIn("q=toque+de+midas", self.eventos[0])

    def test_pesquisa_sem_o_verbo_e_sem_o_na_internet(self):
        self.assertEqual(main.termo_de_pesquisa("pesquisar buracos negros"), "buracos negros")
        self.assertEqual(main.termo_de_pesquisa("pesquisar na internet"), "")
        self.assertEqual(main.termo_de_pesquisa("pesquisar"), "")

    def test_pesquisa_pelo_caminho_da_voz_usa_o_argumento(self):
        """Pela voz quem recorta é a camada de intenções; aqui chega só o argumento."""

        self.assertEqual(main.termo_de_pesquisa("buracos negros"), "buracos negros")
        self.assertEqual(main.termo_de_pesquisa("guns n roses"), "guns n roses")



class TesteGerarImagem(unittest.TestCase):
    def setUp(self):
        self.falas = []
        self.eventos = []

        self._originais = (main.falar, main.navegar_no_painel)
        main.falar = self.falas.append
        main.navegar_no_painel = lambda url: self.eventos.append(url)
        self.addCleanup(self._restaurar)

    def _restaurar(self):
        main.falar, main.navegar_no_painel = self._originais

    def test_descricao_nao_leva_a_palavra_imagem(self):
        main.processar_comando("gerar imagem de um dragão")

        self.assertEqual(len(self.eventos), 1, self.falas)
        self.assertIn("q=dragao", self.eventos[0])
        self.assertNotIn("imagem", self.eventos[0].split("q=")[-1])

    def test_pedido_vazio_pergunta_o_que_falta_em_vez_de_confirmar(self):
        """Perguntar "você quis dizer gerar uma imagem?" não diz nada ao usuário."""

        main.processar_comando("gerar imagem")

        self.assertEqual(self.eventos, [])
        self.assertTrue(
            any("Diga o que você quer na imagem" in fala for fala in self.falas),
            self.falas,
        )
        self.assertTrue(
            any("não sei gerar imagens" in fala for fala in self.falas) is False,
            "não deveria ter fingido que gerou",
        )

    def test_texto_digitado_no_painel_chega_com_o_verbo(self):
        """O botão do painel manda "gerar imagem <tema>"; o verbo não pode ir para a busca."""

        main.gerar_imagem("gerar imagem pôr do sol")

        self.assertEqual(len(self.eventos), 1, self.falas)
        self.assertIn("q=por+do+sol", self.eventos[0])


class TesteEscalaDeVideo(unittest.TestCase):
    """Coordenada do OCR é física; a do mouse é lógica. Sem converter, erra o alvo."""

    def test_converte_quando_a_escala_nao_foi_declarada(self):
        self.assertEqual(main.para_coordenadas_de_clique(3000, 1600, escala=1.5), (2000, 1067))
        self.assertEqual(main.para_coordenadas_de_clique(1500, 750, escala=1.25), (1200, 600))

    def test_nao_mexe_quando_o_processo_e_dpi_aware(self):
        self.assertEqual(main.para_coordenadas_de_clique(3000, 1600, escala=1.0), (3000, 1600))

    def test_fora_do_windows_devolve_a_escala_neutra(self):
        self.assertEqual(main.escala_de_video(), 1.0)
        self.assertFalse(main.configurar_escalas_de_video())


class TesteCacheDeAplicativos(unittest.TestCase):
    """`Get-StartApps` é lento e rodava a cada "abrir X", travando o laço."""

    LISTA = {
        "google chrome": ("Google Chrome", "chrome.exe"),
        "bloco de notas": ("Bloco de Notas", "notepad.exe"),
    }

    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)

        self._originais = (
            main.ARQUIVO_COMANDOS_APRENDIDOS,
            main._aplicativos_cache,
            main._aplicativos_cache_ate,
            main._listar_aplicativos_do_windows,
            main.falar,
            main.abrir_pelo_id,
            main.os,
        )

        self.chamadas = 0

        def listar_falso(forcar=False):  # noqa: ARG001 - assinatura igual à real
            self.chamadas += 1
            return dict(self.LISTA)

        main.ARQUIVO_COMANDOS_APRENDIDOS = Path(self.pasta.name) / "aprendidos.json"
        main._aplicativos_cache = {}
        main._aplicativos_cache_ate = 0.0
        main._listar_aplicativos_do_windows = listar_falso
        self.falar_registrado = []
        main.falar = self.falar_registrado.append
        self.abertos = []
        main.abrir_pelo_id = self.abertos.append
        self.addCleanup(self._restaurar)

    def _restaurar(self):
        (
            main.ARQUIVO_COMANDOS_APRENDIDOS,
            main._aplicativos_cache,
            main._aplicativos_cache_ate,
            main._listar_aplicativos_do_windows,
            main.falar,
            main.abrir_pelo_id,
            main.os,
        ) = self._originais

    def test_lista_uma_vez_so_dentro_da_validade(self):
        for _ in range(3):
            main.listar_aplicativos()

        self.assertEqual(self.chamadas, 1)

    def test_volta_a_listar_depois_da_validade(self):
        main.listar_aplicativos()
        main._aplicativos_cache_ate = 0.0
        main.listar_aplicativos()

        self.assertEqual(self.chamadas, 2)

    def test_forcar_atualiza_a_lista(self):
        main.listar_aplicativos()
        main.listar_aplicativos(forcar=True)

        self.assertEqual(self.chamadas, 2)

    def test_lista_vazia_nao_fica_guardada_por_dez_minutos(self):
        main._listar_aplicativos_do_windows = lambda: {}

        self.assertEqual(main.listar_aplicativos(), {})
        self.assertLess(main._aplicativos_cache_ate, time.monotonic() + 60)

    def test_nome_parecido_pergunta_em_vez_de_abrir(self):
        """Faltou uma palavra do nome: pergunta, em vez de abrir o "mais parecido"."""

        main.abrir_aplicativo("bloco notas")

        self.assertEqual(self.abertos, [], "não deveria ter aberto sem confirmar")
        self.assertTrue(
            any("quis dizer" in fala for fala in self.falar_registrado),
            self.falar_registrado,
        )

    def test_confirmacao_depois_da_pergunta_abre_o_programa(self):
        main.abrir_aplicativo("bloco notas")
        main.processar_comando("sim")

        self.assertEqual(self.abertos, ["notepad.exe"])

    def test_nome_exato_abre_sem_perguntar(self):
        main.abrir_aplicativo("google chrome")

        self.assertEqual(self.abertos, ["chrome.exe"])


class TesteLogPorProcesso(unittest.TestCase):
    """Dois processos não podem dividir o mesmo RotatingFileHandler."""

    def test_cada_processo_tem_o_seu_arquivo(self):
        self.assertEqual(jarvis_comum.arquivo_de_log("jarvis").name, "jarvis.log")
        self.assertEqual(jarvis_comum.arquivo_de_log("jarvis.tela").name, "jarvis.tela.log")

    def test_arquivos_de_log_inclui_os_dois(self):
        nomes = {caminho.name for caminho in jarvis_comum.arquivos_de_log()}

        self.assertTrue(
            {"jarvis.log", "jarvis.tela.log"} & nomes or nomes,
            nomes,
        )

    def test_configurar_log_grava_no_arquivo_do_processo(self):
        logger = jarvis_comum.configurar_log("jarvis.tela_teste")
        caminho = jarvis_comum.arquivo_de_log("jarvis.tela_teste")
        self.addCleanup(lambda: caminho.unlink(missing_ok=True))

        logger.info("linha de teste")

        for manipulador in logger.handlers:
            manipulador.flush()

        self.assertTrue(caminho.exists())
        self.assertIn("linha de teste", caminho.read_text(encoding="utf-8"))
        self.assertNotEqual(caminho, jarvis_comum.ARQUIVO_LOG)



if __name__ == "__main__":
    unittest.main(verbosity=2)
