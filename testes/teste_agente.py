"""Testes do cérebro com ferramentas (jarvis_agente + jarvis_ferramentas).

Nada aqui toca em rede, microfone, Windows ou interface: o modelo é um
"requisitador" de mentira que devolve respostas prontas do Ollama, e as
ferramentas são funções de mentira que só anotam o que foi chamado. É assim que
dá para testar o laço inteiro em qualquer sistema.

    python -m unittest testes.teste_agente -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "testes"))

from duplas import instalar_dubles

instalar_dubles()

import main
from jarvis_agente import (
    Agente,
    ResultadoDoAgente,
    chamadas_do_texto,
    extrair_chamadas,
)
from jarvis_ferramentas import (
    Ferramenta,
    RegistroDeFerramentas,
    executar_ferramenta,
)


# ---------------------------------------------------------------------------
# Ajudantes
# ---------------------------------------------------------------------------


def resposta_com_chamadas(*chamadas) -> dict:
    """Monta uma resposta do Ollama pedindo ferramentas."""

    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": nome, "arguments": argumentos}}
                for nome, argumentos in chamadas
            ],
        }
    }


def resposta_de_texto(texto: str) -> dict:
    return {"message": {"role": "assistant", "content": texto}}


class _ModeloDeMentira:
    """Devolve as respostas do roteiro, na ordem, e anota os pedidos."""

    def __init__(self, *respostas, erro=None):
        self.respostas = list(respostas)
        self.pedidos: list[dict] = []
        self.erro = erro

    def __call__(self, url, corpo, timeout):
        self.pedidos.append(corpo)

        if self.erro is not None:
            raise self.erro

        if not self.respostas:
            raise AssertionError("o agente pediu mais rodadas do que o roteiro tinha")

        return self.respostas.pop(0)


def registro_de_teste(confirma=False, fala_sozinha=True):
    """Registro com duas ferramentas de mentira."""

    registro = RegistroDeFerramentas()
    chamadas: list[tuple] = []

    def anotar(nome):
        def executar(argumentos):
            chamadas.append((nome, argumentos))
            return f"{nome} executado com {argumentos}"

        return executar

    registro.registrar(
        Ferramenta(
            nome="ferramenta_a",
            descricao="Faz a coisa A",
            executar=anotar("ferramenta_a"),
            parametros={"texto": {"type": "string", "description": "texto"}},
            obrigatorios=("texto",),
            fala_sozinha=fala_sozinha,
        )
    )
    registro.registrar(
        Ferramenta(
            nome="ferramenta_b",
            descricao="Faz a coisa B",
            executar=anotar("ferramenta_b"),
        )
    )
    registro.registrar(
        Ferramenta(
            nome="ferramenta_perigosa",
            descricao="Muda algo do usuário",
            executar=anotar("ferramenta_perigosa"),
            confirma=confirma or True,
            pedido_de_confirmacao="mexa no seu arquivo",
        )
    )

    return registro, chamadas


# ---------------------------------------------------------------------------
# 1. Registro de ferramentas
# ---------------------------------------------------------------------------


class TesteRegistroDeFerramentas(unittest.TestCase):
    def test_esquema_sai_no_formato_do_ollama(self):
        registro, _ = registro_de_teste()
        esquema = registro.obter("ferramenta_a").esquema()

        self.assertEqual(esquema["type"], "function")
        self.assertEqual(esquema["function"]["name"], "ferramenta_a")
        self.assertEqual(esquema["function"]["parameters"]["required"], ["texto"])
        self.assertIn("texto", esquema["function"]["parameters"]["properties"])

    def test_ferramenta_repetida_e_recusada(self):
        registro, _ = registro_de_teste()

        with self.assertRaises(ValueError):
            registro.registrar(
                Ferramenta(nome="ferramenta_a", descricao="outra", executar=lambda a: "")
            )

    def test_ferramenta_sem_funcao_e_recusada(self):
        registro = RegistroDeFerramentas()

        with self.assertRaises(ValueError):
            registro.registrar(Ferramenta(nome="vazia", descricao="nada"))

    def test_argumento_obrigatorio_faltando_nao_executa(self):
        registro, chamadas = registro_de_teste()

        saida = executar_ferramenta(registro, "ferramenta_a", {})

        self.assertIn("faltou o argumento obrigatório 'texto'", saida)
        self.assertEqual(chamadas, [])

    def test_argumento_obrigatorio_em_branco_nao_executa(self):
        registro, chamadas = registro_de_teste()

        executar_ferramenta(registro, "ferramenta_a", {"texto": "   "})

        self.assertEqual(chamadas, [])

    def test_ferramenta_desconhecida_sugere_parecida(self):
        registro, _ = registro_de_teste()

        saida = executar_ferramenta(registro, "ferramenta_aa", {})

        self.assertIn("desconhecida", saida)
        self.assertIn("ferramenta_a", saida)

    def test_excecao_da_ferramenta_nao_escapa(self):
        registro = RegistroDeFerramentas()
        registro.registrar(
            Ferramenta(
                nome="quebrada",
                descricao="estoura",
                executar=lambda a: 1 / 0,
            )
        )

        saida = executar_ferramenta(registro, "quebrada", {})

        self.assertIn("falhou", saida)
        self.assertIn("ZeroDivisionError", saida)

    def test_guia_em_texto_lista_nomes_e_obrigatorios(self):
        registro, _ = registro_de_teste()

        guia = registro.guia_em_texto()

        self.assertIn("- ferramenta_a(texto (obrigatório))", guia)
        self.assertIn("- ferramenta_b:", guia)


# ---------------------------------------------------------------------------
# 2. Leitura da resposta do modelo
# ---------------------------------------------------------------------------


class TesteLeituraDaResposta(unittest.TestCase):
    def test_argumentos_como_dicionario(self):
        chamadas = extrair_chamadas(resposta_com_chamadas(("a", {"x": 1}))["message"])

        self.assertEqual(chamadas[0].nome, "a")
        self.assertEqual(chamadas[0].argumentos, {"x": 1})

    def test_argumentos_como_texto_json(self):
        mensagem = {"tool_calls": [{"function": {"name": "a", "arguments": '{"x": 1}'}}]}

        chamadas = extrair_chamadas(mensagem)

        self.assertEqual(chamadas[0].argumentos, {"x": 1})

    def test_argumentos_ilegiveis_viram_vazio(self):
        mensagem = {"tool_calls": [{"function": {"name": "a", "arguments": "nao é json"}}]}

        self.assertEqual(extrair_chamadas(mensagem)[0].argumentos, {})

    def test_chamada_sem_nome_e_ignorada(self):
        mensagem = {"tool_calls": [{"function": {"arguments": {"x": 1}}}]}

        self.assertEqual(extrair_chamadas(mensagem), [])

    def test_mensagem_vazia_nao_estoura(self):
        self.assertEqual(extrair_chamadas(None), [])
        self.assertEqual(extrair_chamadas({}), [])

    def test_json_no_meio_do_texto_do_modelo(self):
        texto = 'Claro! {"ferramenta": "ferramenta_b", "argumentos": {}}'

        chamadas = chamadas_do_texto(texto)

        self.assertEqual(chamadas[0].nome, "ferramenta_b")

    def test_texto_sem_json_nao_vira_chamada(self):
        self.assertEqual(chamadas_do_texto("Bom dia, senhor."), [])


# ---------------------------------------------------------------------------
# 3. O laço do agente
# ---------------------------------------------------------------------------


class TesteLacoDoAgente(unittest.TestCase):
    def _agente(self, modelo, registro=None, **extras):
        registro = registro or registro_de_teste()[0]

        return Agente(
            registro=registro,
            modelo="modelo-de-teste",
            url="http://127.0.0.1:11434/api/chat",
            requisitar=modelo,
            **extras,
        )

    def test_ferramenta_e_executada_e_resultado_volta_ao_modelo(self):
        registro, chamadas = registro_de_teste()
        modelo = _ModeloDeMentira(
            resposta_com_chamadas(("ferramenta_a", {"texto": "oi"})),
            resposta_de_texto("Feito."),
        )

        resultado = self._agente(modelo, registro).responder("faz a coisa A")

        self.assertEqual(chamadas, [("ferramenta_a", {"texto": "oi"})])
        self.assertEqual(resultado.texto, "Feito.")
        self.assertEqual(resultado.ferramentas_usadas, ["ferramenta_a"])
        self.assertEqual(resultado.rodadas, 2)

        papeis = [mensagem["role"] for mensagem in modelo.pedidos[1]["messages"]]
        self.assertEqual(papeis, ["system", "user", "assistant", "tool"])

    def test_todas_as_chamadas_do_turno_sao_executadas(self):
        """O erro clássico é executar só a primeira: "abre X e pesquisa Y"."""

        registro, chamadas = registro_de_teste()
        modelo = _ModeloDeMentira(
            resposta_com_chamadas(
                ("ferramenta_a", {"texto": "primeiro"}),
                ("ferramenta_b", {}),
            ),
            resposta_de_texto("Fiz as duas."),
        )

        resultado = self._agente(modelo, registro).responder("faz A e B")

        self.assertEqual([nome for nome, _ in chamadas], ["ferramenta_a", "ferramenta_b"])
        self.assertEqual(len(resultado.passos), 2)

    def test_conversa_pura_nao_executa_nada(self):
        registro, chamadas = registro_de_teste()
        modelo = _ModeloDeMentira(resposta_de_texto("Bom dia!"))

        resultado = self._agente(modelo, registro).responder("bom dia")

        self.assertEqual(chamadas, [])
        self.assertEqual(resultado.texto, "Bom dia!")
        self.assertTrue(resultado.sucesso)
        self.assertEqual(resultado.rodadas, 1)

    def test_as_ferramentas_vão_no_pedido(self):
        registro, _ = registro_de_teste()
        modelo = _ModeloDeMentira(resposta_de_texto("oi"))

        self._agente(modelo, registro).responder("oi")

        nomes = [item["function"]["name"] for item in modelo.pedidos[0]["tools"]]
        self.assertEqual(nomes, ["ferramenta_a", "ferramenta_b", "ferramenta_perigosa"])

    def test_ferramenta_desconhecida_volta_como_resultado(self):
        registro, chamadas = registro_de_teste()
        modelo = _ModeloDeMentira(
            resposta_com_chamadas(("nao_existe", {})),
            resposta_de_texto("Não sei fazer isso."),
        )

        resultado = self._agente(modelo, registro).responder("faz algo estranho")

        self.assertEqual(chamadas, [])
        self.assertEqual(resultado.texto, "Não sei fazer isso.")
        conteudo = modelo.pedidos[1]["messages"][-1]["content"]
        self.assertIn("desconhecida", conteudo)

    def test_ferramenta_que_pede_confirmacao_nao_e_executada(self):
        registro, chamadas = registro_de_teste()
        modelo = _ModeloDeMentira(
            resposta_com_chamadas(("ferramenta_perigosa", {})),
        )

        resultado = self._agente(modelo, registro).responder("mexe no meu arquivo")

        self.assertEqual(chamadas, [])
        self.assertEqual(resultado.confirmacao.nome, "ferramenta_perigosa")
        self.assertEqual(resultado.texto, "")
        self.assertTrue(resultado.sucesso)

    def test_limite_de_rodadas_interrompe_o_ciclo(self):
        registro, chamadas = registro_de_teste()
        modelo = _ModeloDeMentira(
            *[resposta_com_chamadas(("ferramenta_b", {})) for _ in range(6)]
        )

        resultado = self._agente(modelo, registro, max_rodadas=3).responder("faz B sempre")

        self.assertTrue(resultado.erro)
        self.assertEqual(len(chamadas), 3)
        self.assertLessEqual(len(modelo.pedidos), 3)

    def test_limite_de_ferramentas_por_turno(self):
        registro, chamadas = registro_de_teste()
        modelo = _ModeloDeMentira(
            resposta_com_chamadas(("ferramenta_b", {}), ("ferramenta_b", {}), ("ferramenta_b", {})),
            resposta_de_texto("pronto"),
        )

        resultado = self._agente(modelo, registro, max_ferramentas=2).responder("faz B tres vezes")

        self.assertIn("limite de ferramentas", resultado.erro)
        self.assertEqual(len(chamadas), 2)

    def test_erro_de_rede_vira_resultado_sem_excecao(self):
        registro, _ = registro_de_teste()
        modelo = _ModeloDeMentira(erro=OSError("Connection refused"))

        resultado = self._agente(modelo, registro).responder("oi")

        self.assertIn("OSError", resultado.erro)
        self.assertFalse(resultado.sucesso)

    def test_modelo_sem_suporte_a_tools_cai_no_protocolo_de_texto(self):
        registro, chamadas = registro_de_teste()
        respostas = [
            resposta_de_texto('{"ferramenta": "ferramenta_b", "argumentos": {}}'),
            resposta_de_texto("Pronto."),
        ]
        pedidos = []

        def modelo(url, corpo, timeout):
            pedidos.append(corpo)

            if "tools" in corpo:
                raise OSError("registry.ollama.ai/library/x does not support tools")

            return respostas.pop(0)

        resultado = self._agente(modelo, registro).responder("faz a coisa B")

        self.assertEqual([nome for nome, _ in chamadas], ["ferramenta_b"])
        self.assertEqual(resultado.texto, "Pronto.")
        self.assertIn("tools", pedidos[0])
        self.assertNotIn("tools", pedidos[1])
        self.assertIn("Ferramentas disponíveis", pedidos[1]["messages"][0]["content"])

    def test_no_modo_texto_o_guia_entra_no_prompt(self):
        registro, _ = registro_de_teste()
        modelo = _ModeloDeMentira(resposta_de_texto("oi"))

        agente = self._agente(modelo, registro)
        agente.modo_texto = True
        agente.responder("oi")

        sistema = modelo.pedidos[0]["messages"][0]["content"]
        self.assertIn("ferramenta_a", sistema)

    def test_auditoria_guarda_ferramenta_argumento_e_tempo(self):
        registro, _ = registro_de_teste()
        modelo = _ModeloDeMentira(
            resposta_com_chamadas(("ferramenta_a", {"texto": "oi"})),
            resposta_de_texto("Feito."),
        )

        resultado = self._agente(modelo, registro).responder("faz A")

        self.assertEqual(resultado.passos[0].nome, "ferramenta_a")
        self.assertEqual(resultado.passos[0].argumentos, {"texto": "oi"})
        self.assertIn("ferramenta_a", resultado.resumo_da_auditoria())
        self.assertTrue(resultado.identificador)

    def test_executar_injetado_e_usado_no_lugar_do_registro(self):
        registro, chamadas = registro_de_teste()
        modelo = _ModeloDeMentira(
            resposta_com_chamadas(("ferramenta_b", {})),
            resposta_de_texto("Feito."),
        )
        visto = []

        self._agente(modelo, registro).responder(
            "faz B", executar=lambda nome, argumentos: visto.append(nome) or "ok"
        )

        self.assertEqual(visto, ["ferramenta_b"])
        self.assertEqual(chamadas, [])


# ---------------------------------------------------------------------------
# 4. Ferramentas reais da Jarvis
# ---------------------------------------------------------------------------


class TesteFerramentasDaJarvis(unittest.TestCase):
    """O registro do main.py: esquema, nomes e ligação com as funções."""

    def test_registro_do_main_tem_as_ferramentas_esperadas(self):
        registro = main.registro_de_ferramentas()
        esperadas = {
            "abrir_aplicativo", "tocar_musica", "controlar_musica",
            "pesquisar_na_internet", "que_horas_sao", "descrever_tela",
            "clicar_em", "gerar_imagem", "ligar_holograma", "ligar_camera",
            "esconder_painel", "corrigir_texto", "sair_da_jarvis",
            "listar_capacidades", "abrir_navegador", "abrir_youtube_music",
        }

        self.assertEqual(set(registro.nomes()), esperadas)

    def test_montar_duas_vezes_nao_duplica_nem_estoura(self):
        main.montar_ferramentas()
        main.montar_ferramentas()

        self.assertEqual(len(main.registro_de_ferramentas()), 16)

    def test_esquemas_do_main_são_json_valido(self):
        esquemas = main.registro_de_ferramentas().esquemas()

        json.dumps(esquemas)  # não pode levantar
        self.assertEqual(len(esquemas), 16)
        self.assertTrue(all(item["function"]["name"] for item in esquemas))

    def test_ferramentas_que_pedem_confirmacao_dizem_como_pedir(self):
        registro = main.registro_de_ferramentas()

        for nome in ("corrigir_texto", "sair_da_jarvis", "esconder_painel"):
            with self.subTest(nome=nome):
                ferramenta = registro.obter(nome)
                self.assertTrue(ferramenta.confirma)
                self.assertTrue(ferramenta.pedido_de_confirmacao)

    def test_acao_confirmada_e_sempre_uma_intencao_conhecida(self):
        """A confirmação passa pelo fluxo antigo, que precisa de um nome."""

        for ferramenta in main.registro_de_ferramentas().todas():
            if ferramenta.confirma:
                with self.subTest(nome=ferramenta.nome):
                    self.assertTrue(ferramenta.intencao)

    def test_que_horas_sao_fala_e_devolve_a_hora(self):
        registro = main.registro_de_ferramentas()
        falas = []
        original = main.falar
        main.falar = falas.append
        self.addCleanup(lambda: setattr(main, "falar", original))

        saida = executar_ferramenta(registro, "que_horas_sao", {})

        self.assertIn("São ", saida)
        self.assertTrue(falas and falas[0].startswith("Agora são "))

    def test_controlar_musica_traduz_a_acao_do_modelo(self):
        registro = main.registro_de_ferramentas()
        enviadas = []
        original = main.enviar_tecla_de_midia
        main.enviar_tecla_de_midia = lambda tecla, repeticoes=1: enviadas.append(tecla) or True
        self.addCleanup(lambda: setattr(main, "enviar_tecla_de_midia", original))

        saida = executar_ferramenta(registro, "controlar_musica", {"acao": "proxima"})

        self.assertEqual(enviadas, [0xB0])  # próxima faixa
        self.assertIn("proxima", saida)

    def test_controlar_musica_com_acao_inventada_nao_estoura(self):
        registro = main.registro_de_ferramentas()

        saida = executar_ferramenta(registro, "controlar_musica", {"acao": "dançar"})

        self.assertIn("Não reconheci", saida)

    def test_listar_capacidades_nao_fala_sozinha(self):
        """Quem fala é o modelo: a lista inteira lida em voz alta seria longa."""

        ferramenta = main.registro_de_ferramentas().obter("listar_capacidades")

        self.assertFalse(ferramenta.fala_sozinha)

    def test_tocar_musica_monta_o_link_do_servico_pedido(self):
        registro = main.registro_de_ferramentas()
        abertas = []
        original = main.navegar_no_painel
        main.navegar_no_painel = abertas.append
        self.addCleanup(lambda: setattr(main, "navegar_no_painel", original))

        executar_ferramenta(
            registro, "tocar_musica", {"nome": "toca back in black", "servico": "youtube_music"}
        )

        self.assertIn("music.youtube.com", abertas[0])
        self.assertIn("back+in+black", abertas[0])

    def test_abrir_aplicativo_com_argumento_vazio_nao_chama_o_windows(self):
        registro = main.registro_de_ferramentas()
        chamadas = []
        original = main.abrir_aplicativo
        main.abrir_aplicativo = lambda nome: chamadas.append(nome)
        self.addCleanup(lambda: setattr(main, "abrir_aplicativo", original))

        saida = executar_ferramenta(registro, "abrir_aplicativo", {})

        self.assertIn("faltou o argumento", saida)
        self.assertEqual(chamadas, [])


# ---------------------------------------------------------------------------
# 4b. Orçamento de tempo (o turno não pode ficar pendurado)
# ---------------------------------------------------------------------------


class TesteOrcamentoDeTempo(unittest.TestCase):
    """Um turno nunca espera mais do que o orçamento, mesmo com o modelo lento."""

    def test_orcamento_estourado_para_o_turno_antes_de_pedir_de_novo(self):
        registro, chamadas = registro_de_teste()
        relogio = {"agora": 0.0}

        def modelo_lento(url, corpo, timeout):
            # Cada chamada "gasta" 40 s de relógio e o roteiro nunca acaba.
            relogio["agora"] += 40
            return resposta_com_chamadas(("ferramenta_b", {}))

        agente = Agente(
            registro=registro,
            modelo="m",
            url="http://x/api/chat",
            requisitar=modelo_lento,
            relogio=lambda: relogio["agora"],
            orcamento=90.0,
            max_rodadas=10,
        )

        resultado = agente.responder("faz B para sempre")

        self.assertEqual(resultado.motivo, "orcamento")
        self.assertIn("orçamento", resultado.erro)
        self.assertLessEqual(len(chamadas), 3)

    def test_tempo_da_chamada_encolhe_com_o_orcamento_restante(self):
        registro, _ = registro_de_teste()
        tempos: list[float] = []
        relogio = {"agora": 0.0}

        def modelo(url, corpo, timeout):
            tempos.append(timeout)
            relogio["agora"] += 50
            return resposta_de_texto("Pronto.")

        agente = Agente(
            registro=registro,
            modelo="m",
            url="http://x/api/chat",
            requisitar=modelo,
            relogio=lambda: relogio["agora"],
            orcamento=90.0,
            tempo_limite=60.0,
        )

        agente.responder("oi")

        self.assertEqual(tempos[0], 60.0)  # primeira chamada: teto por chamada

    def test_segunda_chamada_usa_so_o_que_sobrou(self):
        registro, _ = registro_de_teste()
        relogio = {"agora": 0.0}
        tempos: list[float] = []

        def modelo(url, corpo, timeout):
            tempos.append(timeout)
            relogio["agora"] += 60

            if len(tempos) == 1:
                return resposta_com_chamadas(("ferramenta_b", {}))

            return resposta_de_texto("Pronto.")

        agente = Agente(
            registro=registro,
            modelo="m",
            url="http://x/api/chat",
            requisitar=modelo,
            relogio=lambda: relogio["agora"],
            orcamento=90.0,
            tempo_limite=60.0,
        )

        agente.responder("faz B e me diga algo")

        self.assertEqual(tempos[0], 60.0)
        self.assertLess(tempos[1], 60.0)  # só os 30 s que sobraram

    def test_ferramenta_travada_e_abandonada_sem_prender_o_turno(self):
        registro, _ = registro_de_teste()
        modelo = _ModeloDeMentira(
            resposta_com_chamadas(("ferramenta_b", {})),
            resposta_de_texto("Não deu para terminar a tempo."),
        )

        def travada(nome, argumentos):
            time.sleep(2)
            return "demorou"

        agente = Agente(
            registro=registro,
            modelo="m",
            url="http://x/api/chat",
            requisitar=modelo,
            tempo_por_ferramenta=0.05,
            tempo_limite=60.0,
        )

        inicio = time.monotonic()
        resultado = agente.responder("faz B", executar=travada)
        gasto = time.monotonic() - inicio

        self.assertLess(gasto, 1.5)  # não esperou os 2 s da ferramenta
        self.assertEqual(resultado.passos[0].nome, "ferramenta_b")
        self.assertIn("demorou mais de", resultado.passos[0].resultado)
        self.assertEqual(resultado.texto, "Não deu para terminar a tempo.")

    def test_ferramenta_que_estoura_no_teto_nao_derruba_o_turno(self):
        registro, _ = registro_de_teste()
        modelo = _ModeloDeMentira(
            resposta_com_chamadas(("ferramenta_b", {})),
            resposta_de_texto("Segui sem ela."),
        )

        def quebrada(nome, argumentos):
            raise ValueError("estourou")

        agente = Agente(
            registro=registro, modelo="m", url="http://x/api/chat", requisitar=modelo
        )

        resultado = agente.responder("faz B", executar=quebrada)

        self.assertIn("ValueError", resultado.passos[0].resultado)
        self.assertEqual(resultado.texto, "Segui sem ela.")

    def test_erro_de_tempo_do_modelo_e_marcado_como_tempo(self):
        registro, _ = registro_de_teste()
        modelo = _ModeloDeMentira(erro=TimeoutError("timed out"))

        resultado = Agente(
            registro=registro, modelo="m", url="http://x/api/chat", requisitar=modelo
        ).responder("oi")

        self.assertEqual(resultado.motivo, "tempo")

    def test_modelo_fora_do_ar_e_marcado_como_modelo(self):
        registro, _ = registro_de_teste()
        modelo = _ModeloDeMentira(erro=OSError("Connection refused"))

        resultado = Agente(
            registro=registro, modelo="m", url="http://x/api/chat", requisitar=modelo
        ).responder("oi")

        self.assertEqual(resultado.motivo, "modelo")

    def test_teto_de_rodadas_tem_motivo_proprio(self):
        registro, _ = registro_de_teste()
        modelo = _ModeloDeMentira(
            *[resposta_com_chamadas(("ferramenta_b", {})) for _ in range(5)]
        )

        resultado = Agente(
            registro=registro,
            modelo="m",
            url="http://x/api/chat",
            requisitar=modelo,
            max_rodadas=2,
        ).responder("faz B")

        self.assertEqual(resultado.motivo, "rodadas")


# ---------------------------------------------------------------------------
# 5. Roteamento: caminho rápido ou cérebro
# ---------------------------------------------------------------------------


class TesteRoteamentoDoAgente(unittest.TestCase):
    """Integração com o main.py: quem decide o que quando."""

    def setUp(self):
        self.falas: list[str] = []
        self.eventos: list[tuple] = []
        self.consultas: list[str] = []
        self.executadas: list[str] = []
        self.pastas = tempfile.TemporaryDirectory()
        self.addCleanup(self.pastas.cleanup)

        pasta = Path(self.pastas.name)

        self._originais = {
            chave: getattr(main, chave)
            for chave in (
                "ARQUIVO_MEMORIA", "memoria", "ARQUIVO_COMANDOS_APRENDIDOS",
                "comandos_aprendidos", "informar_interface", "falar",
                "responder_com_ollama", "executar_intencao", "carregar_config",
                "listar_aplicativos", "navegar_no_painel", "Agente",
                "agente_ativado", "corrigir_texto_copiado", "intencao_pendente",
            )
        }
        self.addCleanup(self._restaurar)

        main.ARQUIVO_MEMORIA = pasta / "memoria.jsonl"
        main.ARQUIVO_COMANDOS_APRENDIDOS = pasta / "aprendidos.json"
        main.memoria = []
        main.comandos_aprendidos = {}
        main.intencao_pendente = None
        main.ferramenta_pendente = None

        main.falar = self.falas.append
        main.responder_com_ollama = self.consultas.append
        main.informar_interface = lambda tipo, dados=None: self.eventos.append((tipo, dados))
        main.listar_aplicativos = lambda forcar=False: {}
        main.navegar_no_painel = lambda url: None
        main.corrigir_texto_copiado = lambda colar_depois=False: None
        main.carregar_config = lambda: {"agente_ativado": True, "rodadas_do_agente": 3}
        main.agente_ativado = lambda: bool(main.carregar_config().get("agente_ativado", True))

        # Espia o caminho rápido: substituído por um anotador simples.
        original_executar = self._originais["executar_intencao"]

        def espiao(intencao):
            self.executadas.append(intencao.nome)
            return original_executar(intencao)

        main.executar_intencao = espiao

    def _restaurar(self):
        for chave, valor in self._originais.items():
            setattr(main, chave, valor)

    def _ligar_agente(self, modelo):
        """Faz `main.Agente` devolver um agente de mentira com este modelo."""

        def criar(**kwargs):
            kwargs["requisitar"] = modelo
            kwargs.pop("url", None)
            return Agente(url="http://127.0.0.1:11434/api/chat", **kwargs)

        main.Agente = criar
        return modelo

    # -- caminho rápido ----------------------------------------------------

    def test_comando_simples_nao_atravessa_o_agente(self):
        modelo = self._ligar_agente(_ModeloDeMentira())

        main.processar_comando("que horas são?")

        self.assertEqual(modelo.pedidos, [])
        self.assertEqual(self.executadas, ["hora"])

    def test_comando_encadeado_vai_para_o_agente(self):
        modelo = self._ligar_agente(
            _ModeloDeMentira(
                resposta_com_chamadas(
                    ("abrir_navegador", {}),
                    ("pesquisar_na_internet", {"termo": "preço do ssd"}),
                ),
                resposta_de_texto("Deixei o navegador aberto com a pesquisa."),
            )
        )

        main.processar_comando("abre o chrome e pesquisa o preço do ssd")

        self.assertEqual(len(modelo.pedidos), 2)
        conteudo = [mensagem["content"] for mensagem in modelo.pedidos[1]["messages"]]
        self.assertTrue(any("Navegador aberto" in texto for texto in conteudo))

    def test_agente_desligado_usa_o_caminho_antigo(self):
        main.carregar_config = lambda: {"agente_ativado": False}
        modelo = self._ligar_agente(_ModeloDeMentira())

        main.processar_comando("fala uma coisa qualquer para mim")

        self.assertEqual(modelo.pedidos, [])
        self.assertEqual(self.consultas, ["fala uma coisa qualquer para mim"])

    # -- agente no comando de conversa -------------------------------------

    def test_pergunta_livre_usa_o_agente(self):
        self._ligar_agente(_ModeloDeMentira(resposta_de_texto("Tudo ótimo.")))

        main.processar_comando("bom dia jarvis, tudo bem?")

        self.assertEqual(self.falas, ["Tudo ótimo."])
        self.assertEqual(self.consultas, [])
        self.assertIn(("estado_voz", {"processando": False}), self.eventos)

    def test_resposta_do_agente_aparece_no_painel(self):
        self._ligar_agente(_ModeloDeMentira(resposta_de_texto("Tudo ótimo.")))

        main.processar_comando("bom dia")

        self.assertIn(("Jarvis", "Tudo ótimo."), [(m["tipo"], m["texto"]) for m in main.memoria])

    def test_demora_avisa_em_vez_de_reconsultar_a_conversa(self):
        """Turno que estourou o tempo pede desculpa; não recomeça do zero."""

        class AgenteLento:
            def __init__(self, **kwargs):
                pass

            def responder(self, pergunta):
                return ResultadoDoAgente(
                    erro="o turno passou do orçamento de 90 s", motivo="orcamento"
                )

        original = main.Agente
        main.Agente = AgenteLento
        self.addCleanup(lambda: setattr(main, "Agente", original))

        main.processar_comando("fala uma coisa qualquer para mim")

        self.assertEqual(len(self.falas), 1)
        self.assertIn("demorando mais do que devia", self.falas[0])
        self.assertEqual(self.consultas, [])

    def test_agente_sem_resposta_cai_na_conversa_antiga(self):
        self._ligar_agente(_ModeloDeMentira(erro=OSError("Connection refused")))

        main.processar_comando("bom dia")

        self.assertEqual(self.consultas, ["bom dia"])
        self.assertEqual(self.falas, [])

    def test_ferramenta_que_fala_sozinha_nao_recebe_fala_dupla(self):
        """As funções antigas já falam: o texto do modelo não pode repetir."""

        self._ligar_agente(
            _ModeloDeMentira(
                resposta_com_chamadas(("que_horas_sao", {})),
                resposta_de_texto("São exatamente quinze e quarenta."),
            )
        )

        main.responder_com_agente("como está o relógio aí nesse instante")

        self.assertEqual(len(self.falas), 1)
        self.assertTrue(self.falas[0].startswith("Agora são "))

    def test_ferramenta_informativa_tem_o_texto_falado(self):
        self._ligar_agente(
            _ModeloDeMentira(
                resposta_com_chamadas(("listar_capacidades", {})),
                resposta_de_texto("Sei abrir programas, tocar música e pesquisar."),
            )
        )

        main.responder_com_agente("me explica tudo o que você consegue fazer aqui")

        self.assertEqual(self.falas, ["Sei abrir programas, tocar música e pesquisar."])

    # -- confirmação -------------------------------------------------------
    #
    # Estes testes chamam `responder_com_agente` direto, de propósito: aqui o
    # que está sob teste é o fluxo de confirmação, não o roteamento. Uma frase
    # que o reconhecedor entende ("pode se encerrar") nem chega ao agente — e há
    # teste separado para isso.

    def test_ferramenta_perigosa_pede_confirmacao_antes(self):
        self._ligar_agente(
            _ModeloDeMentira(resposta_com_chamadas(("sair_da_jarvis", {})))
        )

        continuar = main.responder_com_agente("tchau jarvis, bom descanso")

        self.assertTrue(continuar)
        self.assertEqual(len(self.falas), 1)
        self.assertIn("feche a Jarvis", self.falas[0])
        self.assertEqual(main.ferramenta_pendente.nome, "sair_da_jarvis")

    def test_confirmacao_executa_e_encerra_quando_e_o_sair(self):
        self._ligar_agente(
            _ModeloDeMentira(resposta_com_chamadas(("sair_da_jarvis", {})))
        )
        main.responder_com_agente("tchau jarvis, bom descanso")
        self.falas.clear()

        continuar = main.processar_comando("sim")

        self.assertFalse(continuar)  # encerra o laço da voz
        self.assertIn("Até logo!", self.falas)
        self.assertIsNone(main.ferramenta_pendente)

    def test_confirmacao_recusada_nao_executa(self):
        self._ligar_agente(
            _ModeloDeMentira(resposta_com_chamadas(("esconder_painel", {})))
        )
        main.responder_com_agente("some da tela um pouquinho")
        self.falas.clear()

        main.processar_comando("não")

        self.assertIsNone(main.ferramenta_pendente)
        self.assertIn("não vou fazer", self.falas[0])
        self.assertFalse(
            any(tipo == "esconder_painel" for tipo, _ in self.eventos)
        )

    def test_confirmacao_de_ferramenta_conhecida_passa_pelo_fluxo_antigo(self):
        self._ligar_agente(
            _ModeloDeMentira(resposta_com_chamadas(("esconder_painel", {})))
        )
        main.responder_com_agente("some da tela um pouquinho")
        self.falas.clear()

        main.processar_comando("sim")

        self.assertIn(("esconder_painel", {}), self.eventos)

    # -- detector de encadeamento -----------------------------------------

    def test_detector_de_encadeamento_nos_casos_reais(self):
        casos = {
            "abre o chrome e pesquisa o preço do ssd": True,
            "abre o chrome e toca back in black": True,
            "pesquisa o preço do ssd e depois abre o bloco de notas": True,
            "toca back in black e aumenta o volume": True,
            "abre o chrome": False,
            "toca back in black": False,
            "me diz a hora e o tempo": False,
            "pesquisa sobre buracos negros": False,
        }

        for frase, esperado in casos.items():
            with self.subTest(frase=frase):
                self.assertEqual(
                    main.eh_comando_encadeado(main.normalizar(frase)), esperado
                )


if __name__ == "__main__":
    unittest.main()
