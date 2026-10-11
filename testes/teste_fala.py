"""Testes do motor de fala persistente (jarvis_tts.py).

O PowerShell em si não dá para testar aqui (não existe fora do Windows), mas
o **protocolo** entre o Python e o processo de fala dá — e é justamente onde
um erro passaria despercebido.
"""

from __future__ import annotations

import base64
import io
import threading
import unittest

from jarvis_tts import MARCA_FALANDO, MARCA_LIVRE, MotorDeFala, _codificar


class _EntradaFalsa(io.BytesIO):
    """stdin do processo falso: guarda o que o Python escreveu."""

    def __init__(self):
        super().__init__()
        self.fechado = False

    @property
    def closed(self):
        return self.fechado

    def write(self, dados):
        if self.fechado:
            raise ValueError("I/O operation on closed file")

        return super().write(dados)

    def close(self):
        self.fechado = True


class _ProcessoFalso:
    """Dublê do subprocess: aceita comandos e responde o protocolo."""

    def __init__(self, marcas=(MARCA_LIVRE,)):
        self.stdin = _EntradaFalsa()
        self.stderr = io.BytesIO(b"")
        self.stdout = io.BytesIO("".join(marca + "\n" for marca in marcas).encode("ascii"))
        self.returncode = None
        self.morto = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        self.returncode = -9

    # -- ajudantes de teste -------------------------------------------------

    def linhas_enviadas(self) -> list[str]:
        self.stdin.seek(0)
        return [linha for linha in self.stdin.read().decode("ascii").splitlines() if linha]


class TesteCodificacao(unittest.TestCase):
    def test_texto_vai_e_volta_com_acentos(self):
        original = "Olá, Járvis! São 14:32 — não esquece o café."
        codificado = _codificar(original)

        self.assertEqual(base64.b64decode(codificado).decode("utf-8"), original)

    def test_frase_vazia_nao_usa_o_processo(self):
        motor = MotorDeFala()

        self.assertTrue(motor.falar(""))
        self.assertIsNone(motor._processo)


class TesteForaDoWindows(unittest.TestCase):
    def test_sem_windows_o_motor_avisa_que_nao_da(self):
        motor = MotorDeFala()

        if motor.disponivel:
            self.skipTest("este teste é para sistemas sem System.Speech")

        self.assertFalse(motor.falar("bom dia"))
        self.assertIn("Windows", motor.ultimo_erro)
        self.assertFalse(motor.parar())
        self.assertFalse(motor.ativo())


class TesteProtocoloComProcessoFalso(unittest.TestCase):
    """Exercita o mesmo caminho do processo real, com um dublê no lugar dele."""

    def _motor_com_processo(self, marcas=(MARCA_LIVRE,)):
        motor = MotorDeFala()
        falso = _ProcessoFalso(marcas)
        motor._processo = falso

        # Lê as marcas que o processo já mandou (equivalente à thread leitora).
        motor._ler_saida()

        return motor, falso

    def test_frase_e_enviada_em_base64(self):
        motor, falso = self._motor_com_processo()

        self.assertTrue(motor.falar("toque acdc"))
        self.assertEqual(falso.linhas_enviadas(), [_codificar("toque acdc")])
        self.assertEqual(motor.frases_faladas, 1)

    def test_parar_manda_o_comando_de_interrupcao(self):
        motor, falso = self._motor_com_processo(marcas=(MARCA_FALANDO,))

        self.assertTrue(motor.parar())
        self.assertEqual(falso.linhas_enviadas(), ["!parar"])
        self.assertFalse(motor.falando())

    def test_fechar_manda_sair_e_esquece_o_processo(self):
        motor, falso = self._motor_com_processo()

        motor.fechar()

        self.assertEqual(falso.linhas_enviadas(), ["!sair"])
        self.assertIsNone(motor._processo)
        self.assertFalse(motor.ativo())

    def test_marca_livre_limpa_o_estado_de_fala(self):
        motor, _ = self._motor_com_processo(marcas=(MARCA_FALANDO, MARCA_LIVRE))
        self.assertFalse(motor.falando())

    def test_processo_morto_faz_o_envio_falhar(self):
        motor, falso = self._motor_com_processo()
        falso.returncode = 1  # morreu

        self.assertFalse(motor.falar("oi"))
        self.assertFalse(motor.ativo())

    def test_escrita_quebrada_nao_levanta_excecao(self):
        motor, falso = self._motor_com_processo()
        falso.stdin.close()

        self.assertFalse(motor.falar("oi"))
        self.assertIn("texto", motor.ultimo_erro.lower())


class _SaidaQueSegura:
    """Finge um processo que já falou/aliviou e continua vivo."""

    def __init__(self, marcas, evento):
        self._marcas = list(marcas)
        self._evento = evento

    def __iter__(self):
        for marca in self._marcas:
            yield (marca + "\n").encode("ascii")

        self._evento.wait(2)


class TesteLeitorEmThread(unittest.TestCase):
    def test_estado_de_fala_acompanha_as_marcas(self):
        motor = MotorDeFala()
        evento = threading.Event()
        falso = _ProcessoFalso()
        falso.stdout = _SaidaQueSegura((MARCA_FALANDO,), evento)
        motor._processo = falso

        leitor = threading.Thread(target=motor._ler_saida, daemon=True)
        leitor.start()

        self.assertTrue(self._esperar(motor.falando), "deveria ficar marcado como falando")

        evento.set()
        leitor.join(timeout=2)

        self.assertFalse(motor.falando())

    @staticmethod
    def _esperar(condicao, tempo=2.0):
        import time

        limite = time.monotonic() + tempo

        while time.monotonic() < limite:
            if condicao():
                return True
            time.sleep(0.02)

        return False

    def test_a_thread_leitora_entende_as_marcas(self):
        motor = MotorDeFala()
        falso = _ProcessoFalso(marcas=(MARCA_FALANDO,))
        motor._processo = falso

        leitor = threading.Thread(target=motor._ler_saida, daemon=True)
        leitor.start()
        leitor.join(timeout=2)

        self.assertFalse(leitor.is_alive())
        # Com o fluxo terminado, a thread deixa o estado limpo.
        self.assertFalse(motor.falando())


if __name__ == "__main__":
    unittest.main()
