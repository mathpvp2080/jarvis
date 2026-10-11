"""Testes da cadeia de reconhecimento de voz (jarvis_voz.py).

Nenhum motor real é usado: motores falsos são injetados, então os testes
rodam em qualquer sistema, sem internet, sem Windows e sem microfone.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis_voz import (  # noqa: E402
    INTERVALO_MINIMO_ENTRE_AVISOS,
    LIMITE_DE_FALHAS_PARA_TROCAR,
    GerenciadorDeVoz,
    ResultadoDeVoz,
    _parece_falha_de_rede,
)

AUDIO = np.zeros(16000, dtype=np.int16)


class ErroDeRede(Exception):
    """Equivalente ao RequestError do speech_recognition."""


class RequestError(Exception):
    """Mesmo nome da exceção real: é o que o classificador procura."""


class MotoresFalsos:
    """Motores controláveis: cada um devolve o que o teste mandar."""

    def __init__(self, respostas: dict):
        self.respostas = respostas
        self.chamadas: list[str] = []

    def _responder(self, nome):
        self.chamadas.append(nome)
        resposta = self.respostas.get(nome)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    def google(self, audio):
        return self._responder("google")

    def whisper(self, audio):
        return self._responder("whisper")

    def vosk(self, audio):
        return self._responder("vosk")

    def windows(self, audio):
        return self._responder("windows")


def gerenciador(respostas: dict, disponiveis=("google", "whisper"), config=None):
    gerente = GerenciadorDeVoz(config=config or {}, motores=MotoresFalsos(respostas))
    gerente.disponiveis = lambda: list(disponiveis)  # fixa o cenário do teste
    return gerente


class TesteCadeiaDeMotores(unittest.TestCase):
    def test_usa_o_principal_quando_funciona(self):
        gerente = gerenciador({"google": "abrir o chrome"})
        resultado = gerente.transcrever(AUDIO)

        self.assertTrue(resultado.ok)
        self.assertEqual(resultado.texto, "abrir o chrome")
        self.assertEqual(resultado.motor, "google")
        self.assertEqual(gerente.motores.chamadas, ["google"])

    def test_cai_para_o_plano_b_quando_o_principal_falha(self):
        """Regressão do bug B2: sem internet o Jarvis ficava surdo."""

        eram_erros = {"google": ErroDeRede("connection refused")}
        eram_erros.update({"whisper": "tocar acdc"})
        gerente = gerenciador(eram_erros, disponiveis=("google", "whisper"))

        resultado = gerente.transcrever(AUDIO)

        self.assertTrue(resultado.ok)
        self.assertEqual(resultado.texto, "tocar acdc")
        self.assertEqual(resultado.motor, "whisper")
        self.assertEqual(gerente.motores.chamadas, ["google", "whisper"])

    def test_modo_offline_entra_depois_de_falhas_seguidas(self):
        """Com tudo dependendo de rede, o Jarvis passa a priorizar o offline."""

        fallback = {"google": ErroDeRede("timed out"), "whisper": ErroDeRede("timed out")}
        gerente = gerenciador(fallback, disponiveis=("google", "whisper"))

        # Enquanto nada funciona, o principal continua sendo tentado primeiro.
        for _ in range(LIMITE_DE_FALHAS_PARA_TROCAR):
            self.assertFalse(gerente.transcrever(AUDIO).ok)

        self.assertEqual(gerente.motores.chamadas[0], "google")
        self.assertTrue(gerente.em_modo_offline())

        # Com o motor local de volta, ele é tentado primeiro — sem internet.
        gerente.motores.respostas["whisper"] = "tocar acdc"
        gerente.motores.chamadas.clear()

        resultado = gerente.transcrever(AUDIO)

        self.assertEqual(gerente.motores.chamadas[0], "whisper")
        self.assertTrue(resultado.ok)
        self.assertEqual(resultado.motor, "whisper")

    def test_fala_nao_entendida_nao_e_confundida_com_falha_de_rede(self):
        gerente = gerenciador({"google": ""}, disponiveis=("google",))
        resultado = gerente.transcrever(AUDIO)

        self.assertTrue(resultado.falhou)
        self.assertFalse(resultado.falha_de_rede)
        self.assertIn("entender", gerente.mensagem_para_usuario(resultado))

    def test_mensagem_de_rede_quando_nao_ha_plano_b(self):
        gerente = gerenciador(
            {"google": RequestError("getaddrinfo failed")}, disponiveis=("google",)
        )
        resultado = gerente.transcrever(AUDIO)

        self.assertTrue(resultado.falha_de_rede)
        mensagem = gerente.mensagem_para_usuario(resultado)
        self.assertIn("internet", mensagem)

    def test_motor_preferido_pelo_usuario_manda(self):
        gerente = gerenciador(
            {"google": "pelo google", "whisper": "pelo whisper"},
            disponiveis=("google", "whisper"),
            config={"motor_de_voz": "whisper"},
        )
        resultado = gerente.transcrever(AUDIO)

        self.assertEqual(resultado.motor, "whisper")
        self.assertEqual(gerente.motores.chamadas, ["whisper"])

    def test_nao_repete_aviso_em_loop(self):
        """Eram 81 mensagens iguais num único dia no histórico."""

        gerente = gerenciador({"google": ErroDeRede("x")}, disponiveis=("google",))

        self.assertTrue(gerente.deve_avisar_agora())
        self.assertFalse(gerente.deve_avisar_agora())

        gerente.ultimo_aviso -= INTERVALO_MINIMO_ENTRE_AVISOS + 1
        self.assertTrue(gerente.deve_avisar_agora())

    def test_audio_vazio_nao_chama_motor(self):
        gerente = gerenciador({"google": "algo"})
        resultado = gerente.transcrever(None)

        self.assertTrue(resultado.falhou)
        self.assertEqual(gerente.motores.chamadas, [])


class TesteClassificacaoDeErro(unittest.TestCase):
    def test_reconhece_erros_de_rede(self):
        self.assertTrue(_parece_falha_de_rede(RequestError("x"), "qualquer coisa"))
        self.assertTrue(_parece_falha_de_rede(ValueError("x"), "HTTPSConnectionPool: timeout"))
        self.assertTrue(_parece_falha_de_rede(ValueError("x"), "getaddrinfo failed"))
        self.assertFalse(_parece_falha_de_rede(ValueError("x"), "nenhuma alternativa"))


class TesteResultado(unittest.TestCase):
    def test_ok_so_com_texto(self):
        self.assertFalse(ResultadoDeVoz().ok)
        self.assertFalse(ResultadoDeVoz(texto="oi", falhou=True).ok)
        self.assertTrue(ResultadoDeVoz(texto="oi").ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
