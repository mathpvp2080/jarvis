"""Testes do Jarvis que rodam em qualquer sistema, sem microfone e sem Windows.

Como rodar:
    python -m unittest discover -s testes -v

Motivo de existirem: os problemas relatados ("às vezes não me escuta", "às
vezes ignora o comando", "o painel mostra mensagem que não existe") vinham de
algoritmos que ninguém conseguia testar. Aqui as partes puras ficaram cobertas.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis_audio import (  # noqa: E402
    BLOCOSIZE,
    DetectorDeVoz,
    FiltroPassaAlta,
    escolher_microfone,
    listar_microfones,
)
from jarvis_comum import FilaDeArquivos, escrever_json_atomico, ler_json  # noqa: E402


# ---------------------------------------------------------------------------
# Sinais sintéticos
# ---------------------------------------------------------------------------


def ruido(rms: float, blocos: int = 1, semente: int | None = 1) -> list[np.ndarray]:
    """Ruído branco (chiado de fundo) com o RMS pedido."""

    gerador = np.random.default_rng(semente)
    saida = []
    for _ in range(blocos):
        amostras = gerador.normal(0.0, 1.0, BLOCOSIZE)
        amostras = amostras / max(np.sqrt(np.mean(amostras**2)), 1e-9) * rms
        saida.append(np.clip(amostras, -32768, 32767).astype(np.int16))
    return saida


def tom(frequencia: float, rms: float, blocos: int = 1) -> list[np.ndarray]:
    """Tom puro — representa ventilador/ar-condicionado (grave constante)."""

    saida = []
    for indice in range(blocos):
        t = np.arange(BLOCOSIZE) + indice * BLOCOSIZE
        onda = np.sin(2 * np.pi * frequencia * t / 16000)
        onda = onda / max(np.sqrt(np.mean(onda**2)), 1e-9) * rms
        saida.append(np.clip(onda, -32768, 32767).astype(np.int16))
    return saida


def fala(rms: float, blocos: int = 1, semente: int | None = 7) -> list[np.ndarray]:
    """Fala sintética: tons entre 400 e 2800 Hz, com envelope de sílabas.

    O envelope (sílabas de ~180 ms separadas por pausas curtas) é o que
    diferencia fala de um ruído constante — é justamente o que o detector usa
    para não confundir ventilador com voz. A normalização é feita no sinal
    inteiro, nunca por bloco (normalizar por bloco apagaria o envelope).
    """

    gerador = np.random.default_rng(semente)
    total = blocos * BLOCOSIZE
    t = np.arange(total) / 16000

    onda = np.zeros(total)
    for frequencia in (400, 700, 1200, 2000, 2800):
        onda += np.sin(2 * np.pi * frequencia * t + gerador.uniform(0, 6.28))

    # Sílabas de 170 ms; a cada 1,0 s vem uma pausa de 250 ms (como na fala real).
    posicao = t % 0.17
    envelope = np.where(posicao < 0.145, 1.0, 0.05)
    pausa = (t % 1.0) > 0.85
    envelope = np.where(pausa, 0.01, envelope)
    onda = onda * envelope
    onda = onda / max(np.sqrt(np.mean(onda**2)), 1e-9) * rms

    return [
        np.clip(onda[i : i + BLOCOSIZE], -32768, 32767).astype(np.int16)
        for i in range(0, total, BLOCOSIZE)
    ]


def alimentar(detector: DetectorDeVoz, blocos: list[np.ndarray]) -> list[np.ndarray]:
    frases = []
    for bloco in blocos:
        frase = detector.processar(bloco)
        if frase is not None:
            frases.append(frase)
    return frases


# ---------------------------------------------------------------------------
# Filtro
# ---------------------------------------------------------------------------


class TesteFiltroPassaAlta(unittest.TestCase):
    def test_corta_grave_e_preserva_voz(self):
        filtro = FiltroPassaAlta(frequencia=250.0, taxa=16000)

        grave = np.concatenate(tom(120, 600, blocos=8)).astype(np.float32)
        voz = np.concatenate(fala(600, blocos=8)).astype(np.float32)

        def rms(dados):
            return float(np.sqrt(np.mean(dados**2)))

        # Aquece o filtro (transitório inicial não conta).
        filtro.processar(grave[:BLOCOSIZE])
        grave_filtrado = filtro.processar(grave[BLOCOSIZE:])
        filtro.reiniciar()
        filtro.processar(voz[:BLOCOSIZE])
        voz_filtrada = filtro.processar(voz[BLOCOSIZE:])

        atenuacao_grave = rms(grave_filtrado) / rms(grave[BLOCOSIZE:])
        atenuacao_voz = rms(voz_filtrada) / rms(voz[BLOCOSIZE:])

        self.assertLess(atenuacao_grave, 0.5, "o grave deveria ser bem atenuado")
        self.assertGreater(atenuacao_voz, 0.6, "a voz deveria passar quase inteira")
        self.assertGreater(atenuacao_voz / atenuacao_grave, 2.0)


# ---------------------------------------------------------------------------
# Detector de voz
# ---------------------------------------------------------------------------


class TesteDetectorDeVoz(unittest.TestCase):
    def test_sala_silenciosa_com_teclado_nao_dispara(self):
        """Ruído de fundo baixo + tecladas isoladas não podem virar 'fala'."""

        detector = DetectorDeVoz()
        blocos = []
        for indice in range(160):  # ~10 s
            if indice % 7 == 0 and indice > 20:
                blocos.extend(ruido(400, blocos=1, semente=indice))  # uma teclada
            else:
                blocos.extend(ruido(25, blocos=1, semente=indice))

        frases = alimentar(detector, blocos)

        self.assertEqual(len(frases), 0, "teclado/ruído de fundo não pode disparar")
        self.assertGreaterEqual(detector.estatisticas()["piso_ruido"], 12.0)

    def test_fala_em_sala_silenciosa_e_detectada(self):
        detector = DetectorDeVoz()
        blocos = []
        blocos.extend(ruido(25, blocos=15))       # 1 s de silêncio
        blocos.extend(fala(700, blocos=23))       # ~1,5 s de fala
        blocos.extend(ruido(25, blocos=25))       # silêncio para encerrar

        frases = alimentar(detector, blocos)

        self.assertEqual(len(frases), 1)
        duracao = len(frases[0]) / 16000
        self.assertGreater(duracao, 1.2)
        self.assertLess(duracao, 2.6)

    def test_ambiente_barulhento_ainda_escuta(self):
        """Regressão do bug B8.

        Antes: ventilador grave com RMS 600 subia o limiar acima da voz e a
        Jarvis simplesmente parava de escutar. Agora o grave é cortado antes
        de medir, então a fala continua passando.
        """

        detector = DetectorDeVoz()
        blocos = []
        blocos.extend(tom(120, 600, blocos=30))   # ventilador ligado
        blocos.extend(tom(120, 600, blocos=25))
        blocos[-25:] = fala(900, blocos=25)       # fala por cima do ventilador
        blocos.extend(tom(120, 600, blocos=25))

        frases = alimentar(detector, blocos)

        self.assertEqual(len(frases), 1, "a fala deveria ser capturada mesmo com ventilador")
        estatisticas = detector.estatisticas()
        self.assertLessEqual(
            estatisticas["limiar"], 1200.0, "o limiar nunca pode passar do teto"
        )

    def test_fala_muito_longa_e_dividida(self):
        detector = DetectorDeVoz()
        blocos = fala(800, blocos=int(24 / 0.064))   # 24 s de fala contínua
        blocos += ruido(25, blocos=25)               # silêncio no fim

        frases = alimentar(detector, blocos)

        self.assertGreaterEqual(len(frases), 2, "fala contínua deve ser cortada em partes")

        for frase in frases:
            self.assertLessEqual(len(frase) / 16000, 20.8)  # teto + pré-buffer

    def test_estalo_curto_e_descartado(self):
        detector = DetectorDeVoz()
        blocos = list(ruido(25, blocos=10))
        blocos.extend(ruido(700, blocos=3))  # ~190 ms: curto demais
        blocos.extend(ruido(25, blocos=20))

        frases = alimentar(detector, blocos)

        self.assertEqual(len(frases), 0)
        self.assertGreaterEqual(detector.estatisticas()["trechos_curtos_descartados"], 1)

    def test_pausar_descarta_o_que_estava_gravando(self):
        detector = DetectorDeVoz()
        alimentar(detector, ruido(25, blocos=10))
        alimentar(detector, fala(800, blocos=6))

        detector.reiniciar()  # simula "Jarvis começou a falar"

        frases = alimentar(detector, fala(800, blocos=20))
        frases += alimentar(detector, ruido(25, blocos=20))

        self.assertEqual(len(frases), 1, "só a fala após o reinício deve valer")
        self.assertLess(len(frases[0]) / 16000, 2.0)


# ---------------------------------------------------------------------------
# Microfone
# ---------------------------------------------------------------------------


class SoundDeviceFalso:
    """Imita o sounddevice para testar a escolha do microfone."""

    def __init__(self, dispositivos, padrao_entrada):
        self._dispositivos = dispositivos
        self.default = type("Padrao", (), {"device": (padrao_entrada, 3)})()

    def query_devices(self):
        return self._dispositivos


class TesteEscolhaDeMicrofone(unittest.TestCase):
    def _instalar(self, dispositivos, padrao):
        falso = SoundDeviceFalso(dispositivos, padrao)
        sys.modules["sounddevice"] = falso
        self.addCleanup(lambda: sys.modules.pop("sounddevice", None))

    def test_ignora_dispositivo_so_de_saida(self):
        self._instalar(
            [
                {"name": "Microphone Array (saída falsa)", "max_input_channels": 0},
                {"name": "Alto-falantes", "max_input_channels": 0},
            ],
            padrao=1,
        )
        indice, nome = escolher_microfone({})
        self.assertIsNone(indice)
        self.assertEqual(listar_microfones(), [])

    def test_prefere_o_padrao_do_sistema(self):
        self._instalar(
            [
                {"name": "Microfone USB", "max_input_channels": 2},
                {"name": "Microfone interno", "max_input_channels": 1},
            ],
            padrao=1,
        )
        indice, nome = escolher_microfone({})
        self.assertEqual(indice, 1)
        self.assertEqual(nome, "Microfone interno")

    def test_respeita_a_escolha_salva_na_configuracao(self):
        self._instalar(
            [
                {"name": "Microfone USB barato", "max_input_channels": 1},
                {"name": "Headset bom", "max_input_channels": 1},
            ],
            padrao=0,
        )
        indice, nome = escolher_microfone({"microfone": "headset"})
        self.assertEqual(indice, 1)
        self.assertEqual(nome, "Headset bom")

        indice_por_numero, _ = escolher_microfone({"microfone": "0"})
        self.assertEqual(indice_por_numero, 0)


# ---------------------------------------------------------------------------
# Fila e JSON
# ---------------------------------------------------------------------------


class TesteFilaDeArquivos(unittest.TestCase):
    def test_nenhuma_mensagem_se_perde(self):
        import tempfile

        with tempfile.TemporaryDirectory() as pasta:
            fila = FilaDeArquivos(Path(pasta) / "fila")
            for indice in range(200):
                self.assertTrue(fila.publicar({"texto": f"comando {indice}"}))

            recebidos = []
            while len(recebidos) < 200:
                lote = fila.consumir(limite=30)
                if not lote:
                    break
                recebidos.extend(lote)

            self.assertEqual(len(recebidos), 200)
            textos = sorted(item["texto"] for item in recebidos)
            self.assertEqual(textos[0], "comando 0")
            self.assertEqual(textos[-1], "comando 99")

    def test_arquivo_corrompido_nao_trava_a_fila(self):
        import tempfile

        with tempfile.TemporaryDirectory() as pasta:
            fila = FilaDeArquivos(Path(pasta) / "fila")
            fila.publicar({"texto": "bom"})
            (Path(pasta) / "fila" / "000-quebrado.json").write_text("{isso não é json")

            itens = fila.consumir()
            self.assertEqual([item["texto"] for item in itens], ["bom"])

    def test_escrita_atomica_nunca_deixa_arquivo_pela_metade(self):
        import tempfile

        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "dados.json"
            self.assertTrue(escrever_json_atomico(caminho, {"a": 1}))
            self.assertEqual(ler_json(caminho, None), {"a": 1})
            self.assertTrue(escrever_json_atomico(caminho, {"a": 2}))
            self.assertEqual(ler_json(caminho, None), {"a": 2})
            self.assertEqual(list(Path(pasta).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
