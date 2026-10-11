"""Testes da camada de intenções (jarvis_intencoes.py).

São os testes que provam o conserto do B11: frases do jeito que as pessoas
falam devem cair no comando certo; conversa livre não deve cair em nada.
"""

from __future__ import annotations

import unittest

from jarvis_intencoes import (
    LIMIAR_DE_EXECUCAO,
    descrever,
    normalizar,
    quer_ligar,
    reconhecer,
)


def _nomes(texto):
    intencao = reconhecer(texto)
    return intencao.nome if intencao else None


class TesteSinonimosDeVerbo(unittest.TestCase):
    def test_abre_abra_e_abrir_dao_no_mesmo_lugar(self):
        for frase in ("abrir o chrome", "abre o chrome", "abra o chrome", "abri o chrome"):
            with self.subTest(frase=frase):
                intencao = reconhecer(frase)

                self.assertIsNotNone(intencao, frase)
                self.assertEqual(intencao.nome, "abrir_aplicativo")
                self.assertEqual(intencao.argumento, "chrome")

    def test_outros_verbos_de_abrir(self):
        for frase in ("inicia o spotify", "roda a steam", "executa o discord", "chama o bloco de notas"):
            with self.subTest(frase=frase):
                self.assertEqual(_nomes(frase), "abrir_aplicativo")

    def test_pedido_educado_nao_engana(self):
        intencao = reconhecer("pode abrir o chrome")

        self.assertEqual(intencao.nome, "abrir_aplicativo")
        self.assertEqual(intencao.argumento, "chrome")


class TesteEnchimento(unittest.TestCase):
    def test_artigos_e_pronomes_sao_ignorados(self):
        for frase in (
            "abre o chrome",
            "abre o chrome para mim",
            "por favor abre o chrome",
            "me abre o chrome",
        ):
            with self.subTest(frase=frase):
                intencao = reconhecer(frase)

                self.assertIsNotNone(intencao, frase)
                self.assertEqual(intencao.nome, "abrir_aplicativo")
                self.assertIn("chrome", intencao.argumento)

    def test_frases_do_historico_que_falhavam(self):
        esperado = {
            "ta vendo minha tela": "descrever_tela",
            "esta vendo minha tela": "descrever_tela",
            "o que estou vendo": "descrever_tela",
            "descreva o que esta na minha tela": "descrever_tela",
            "ler minha tela": "descrever_tela",
            "para ligar a camera": "camera",
            "tira holograma para mim": "holograma",
            "desativa o holograma": "holograma",
            "quero um holograma": "holograma",
            "esconder painel": "esconder_painel",
            "esconde o painel para mim": "esconder_painel",
        }

        for frase, nome in esperado.items():
            with self.subTest(frase=frase):
                intencao = reconhecer(frase)

                self.assertIsNotNone(intencao, frase)
                self.assertEqual(intencao.nome, nome)
                self.assertGreaterEqual(
                    intencao.confianca, LIMIAR_DE_EXECUCAO,
                    f"{frase!r} não deveria precisar de confirmação",
                )


class TesteLigarDesligar(unittest.TestCase):
    def test_holograma(self):
        self.assertTrue(quer_ligar(reconhecer("quero um holograma")))
        self.assertFalse(quer_ligar(reconhecer("tira holograma para mim")))
        self.assertFalse(quer_ligar(reconhecer("desativa o holograma")))

    def test_camera(self):
        self.assertTrue(quer_ligar(reconhecer("para ligar a camera")))
        self.assertFalse(quer_ligar(reconhecer("desliga a camera")))


class TesteRotasConhecidas(unittest.TestCase):
    def test_hora(self):
        for frase in ("que horas são?", "que hora é", "me diga as horas", "qual a hora agora"):
            with self.subTest(frase=frase):
                self.assertEqual(_nomes(frase), "hora")

    def test_musica(self):
        self.assertEqual(_nomes("aumenta o volume"), "musica_controle")
        self.assertEqual(_nomes("aumentar volume"), "musica_controle")
        self.assertEqual(_nomes("proxima musica"), "musica_controle")
        self.assertEqual(_nomes("silenciar"), "musica_controle")

    def test_tocar_musica(self):
        self.assertEqual(_nomes("tocar back in black no youtube music"), "tocar_musica")
        self.assertEqual(_nomes("toca acdc no youtube"), "tocar_musica")

    def test_youtube_music_nao_e_confundido_com_abrir_aplicativo(self):
        intencao = reconhecer("abrir youtube music")

        self.assertEqual(intencao.nome, "abrir_youtube_music")

    def test_pesquisar(self):
        self.assertEqual(_nomes("pesquisar guns n roses no youtube"), "pesquisar")
        self.assertEqual(_nomes("pesquise sobre buracos negros"), "pesquisar")

    def test_clicar_em(self):
        intencao = reconhecer("clicar em confirmar")

        self.assertEqual(intencao.nome, "clicar_em")
        self.assertEqual(intencao.argumento, "confirmar")

    def test_aprender(self):
        self.assertEqual(_nomes("quando eu disser xablau faca que horas sao"), "aprender")


class TesteConfirmacao(unittest.TestCase):
    def test_frase_parecida_pede_confirmacao(self):
        intencao = reconhecer("proxima missa")

        self.assertIsNotNone(intencao)
        self.assertEqual(intencao.nome, "musica_controle")
        self.assertTrue(intencao.precisa_confirmar)
        self.assertIn("música", descrever(intencao))

    def test_horas_entendido_mesmo_faltando_palavra(self):
        intencao = reconhecer("horas sao")

        self.assertEqual(intencao.nome, "hora")

    def test_coisa_diferente_nao_vira_comando(self):
        for frase in ("fechar a porta da sala", "apagar a luz do quarto"):
            with self.subTest(frase=frase):
                intencao = reconhecer(frase)

                if intencao is not None:
                    self.assertTrue(
                        intencao.precisa_confirmar,
                        f"{frase!r} não deveria ser executado sem confirmação",
                    )

    def test_descrever_menciona_o_argumento(self):
        self.assertIn("chrome", descrever(reconhecer("abre o chrome")))


class TesteSemFalsosPositivos(unittest.TestCase):
    def test_conversa_livre_nao_vira_comando(self):
        falas = (
            "me conta uma piada",
            "jefferson",
            "qual é a capital da França",
            "quem foi albert einstein",
            "como você está hoje",
            "obrigado",
            "bom dia",
            "escreve um poema sobre o mar",
            "transcreva um recado",
            "o brasil vai jogar hoje",
        )

        for frase in falas:
            with self.subTest(frase=frase):
                self.assertIsNone(
                    reconhecer(frase),
                    f"{frase!r} deveria ir para a conversa livre, não para um comando",
                )

    def test_afirmacoes_e_negacoes_sao_reconhecidas(self):
        self.assertEqual(_nomes("sim"), "confirmar")
        self.assertEqual(_nomes("não quero"), "cancelar")
        self.assertEqual(_nomes("deixa pra lá"), "cancelar")

    def test_negacao_nao_engole_frase_com_verbo(self):
        intencao = reconhecer("não quero abrir o chrome")

        self.assertEqual(intencao.nome, "abrir_aplicativo")


class TesteNormalizacao(unittest.TestCase):
    def test_normalizar(self):
        self.assertEqual(normalizar("Olá, Járvis! Tô aqui?"), "ola jarvis to aqui")

    def test_frase_vazia(self):
        self.assertIsNone(reconhecer(""))
        self.assertIsNone(reconhecer("   "))
        self.assertIsNone(reconhecer(None))


if __name__ == "__main__":
    unittest.main()
