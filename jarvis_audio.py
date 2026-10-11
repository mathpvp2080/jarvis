"""Captura de áudio e detecção de fala do Jarvis.

Por que este módulo existe
--------------------------
No ``main.py`` original, a captura e a detecção de fala ficavam dentro de um
laço bloqueante: enquanto ele "ouvia", nada mais era processado (um comando
digitado no painel podia esperar até 60 s) e o piso de ruído era calculado de
um jeito que **só podia subir** — em ambiente barulhento o limiar passava da
fala humana e a Jarvis parava de escutar para sempre; em ambiente silencioso,
ficava no mínimo e disparava com teclado/ventilador.

Aqui a lógica virou uma classe pura (``DetectorDeVoz``), sem I/O, o que
permite testá-la com sinais sintéticos em qualquer sistema — veja
``testes/teste_audio.py``.

O ``ThreadOuvinte`` junta tudo: abre o microfone, roda a detecção fora do laço
principal e entrega frases completas numa fila.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque

import numpy as np

TAXA_DE_AUDIO = 16000
BLOCOSIZE = 1024
DURACAO_BLOCO = BLOCOSIZE / TAXA_DE_AUDIO  # 64 ms


class FiltroPassaAlta:
    """Filtro biquad (Butterworth) de 2ª ordem, para análise de nível.

    Detalhe importante: o filtro é usado **só para decidir** se há fala. O
    áudio enviado ao reconhecimento continua sendo o original, sem filtro.
    Isso permite cortar o grave (ventilador, ar-condicionado, zumbido, ruído
    de fundo) sem prejudicar a inteligibilidade.
    """

    def __init__(self, frequencia: float = 250.0, taxa: int = TAXA_DE_AUDIO, q: float = 0.7071):
        from math import cos, pi, sin

        w0 = 2.0 * pi * frequencia / taxa
        cos_w0 = cos(w0)
        sin_w0 = sin(w0)
        alpha = sin_w0 / (2.0 * q)

        b0 = (1.0 + cos_w0) / 2.0
        b1 = -(1.0 + cos_w0)
        b2 = b0
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha

        self.b0, self.b1, self.b2 = b0 / a0, b1 / a0, b2 / a0
        self.a1, self.a2 = a1 / a0, a2 / a0

        self.x1 = self.x2 = self.y1 = self.y2 = 0.0

    def processar(self, x: np.ndarray) -> np.ndarray:
        y = np.empty_like(x)
        b0, b1, b2, a1, a2 = self.b0, self.b1, self.b2, self.a1, self.a2
        x1, x2, y1, y2 = self.x1, self.x2, self.y1, self.y2

        for indice in range(x.size):
            entrada = float(x[indice])
            saida = b0 * entrada + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1 = x1, entrada
            y2, y1 = y1, saida
            y[indice] = saida

        self.x1, self.x2, self.y1, self.y2 = x1, x2, y1, y2
        return y

    def reiniciar(self) -> None:
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0


class DetectorDeVoz:
    """Segmenta fala em blocos de áudio, com piso de ruído adaptativo.

    Entrada: blocos int16 mono (como vêm do microfone).
    Saída: ``processar()`` devolve um ``np.ndarray`` int16 quando uma frase
    termina, ou ``None`` enquanto nada terminou.

    Como o piso de ruído é estimado
    -------------------------------
    1. **Aprendizado só no silêncio**: enquanto algo parece fala, o piso não
       se move. Assim a sua voz nunca infla o piso e ensurdece o detector
       (defeito que aparecia em ditação longa).
    2. **Estimativa rápida no início**: se a sala já está barulhenta quando o
       Jarvis abre, o piso acompanha nos primeiros blocos.
    3. **Limiar com teto**: nunca fica alto demais para a voz passar.
    4. **Vigia de ruído contínuo**: ventilador/ar-condicionado que fica acima
       do limiar sem nenhuma queda é reconhecido pela ausência de variação
       (fala tem sílabas; ruído não) e o piso é recalibrado para o ambiente.
    """

    def __init__(
        self,
        taxa: int = TAXA_DE_AUDIO,
        limiar_minimo: float = 45.0,
        multiplicador_ruido: float = 1.8,
        teto_limiar: float = 1200.0,
        piso_ruido_inicial: float = 60.0,
        piso_ruido_minimo: float = 12.0,
        piso_ruido_maximo: float = 4000.0,
        blocos_para_iniciar: int = 2,
        silencio_para_encerrar: float = 1.0,
        tempo_maximo_fala: float = 20.0,
        tempo_minimo_fala: float = 0.25,
        pre_roll_segundos: float = 0.5,
        blocos_de_estimativa_inicial: int = 8,
        tempo_maximo_ruido_continuo: float = 6.0,
        tempo_maximo_ruido_continuo_no_inicio: float = 2.5,
        segundos_de_aquecimento: float = 20.0,
        variacao_minima_para_fala: float = 0.20,
        usar_filtro_passa_alta: bool = True,
    ):
        self.taxa = taxa
        self.limiar_minimo = limiar_minimo
        self.multiplicador_ruido = multiplicador_ruido
        self.teto_limiar = teto_limiar
        self.piso_ruido_minimo = piso_ruido_minimo
        self.piso_ruido_maximo = piso_ruido_maximo
        self.blocos_para_iniciar = blocos_para_iniciar
        self.silencio_para_encerrar = silencio_para_encerrar
        self.tempo_maximo_fala = tempo_maximo_fala
        self.tempo_minimo_fala = tempo_minimo_fala
        self.blocos_de_estimativa_inicial = blocos_de_estimativa_inicial
        self.tempo_maximo_ruido_continuo = tempo_maximo_ruido_continuo
        self.tempo_maximo_ruido_continuo_no_inicio = tempo_maximo_ruido_continuo_no_inicio
        self.segundos_de_aquecimento = segundos_de_aquecimento
        self.variacao_minima_para_fala = variacao_minima_para_fala
        self.usar_filtro_passa_alta = usar_filtro_passa_alta

        self.piso_ruido = max(piso_ruido_inicial, piso_ruido_minimo)
        self.pre_roll_blocos = max(1, int(round(pre_roll_segundos / DURACAO_BLOCO)))

        self._filtro = FiltroPassaAlta(taxa=taxa)
        self._blocos_acima = 0
        self._falando = False
        self._inicio_fala = 0.0
        self._ultimo_bloco_acima = 0.0
        self._anteriores: deque = deque(maxlen=self.pre_roll_blocos)
        self._gravados: list[np.ndarray] = []
        self._contador = 0
        self._blocos_descartados_curtos = 0
        self._inicio_acima_continuo = 0.0
        self._recalibracoes = 0
        self._niveis_acima: deque = deque(maxlen=48)
        self._minimo_acima = float("inf")

        self.nivel_atual = 0.0
        self.limiar_atual = limiar_minimo
        self.ultimo_motivo = "aguardando"

    # -- estimativas -------------------------------------------------------

    def _filtrar(self, amostras: np.ndarray) -> np.ndarray:
        """Aplica o passa-alta usado apenas para medir o nível."""

        if not self.usar_filtro_passa_alta or amostras.size == 0:
            return amostras

        return self._filtro.processar(amostras)

    def _atualizar_piso(self, nivel: float, acima_do_limiar: bool) -> None:
        """Acompanha o nível de ruído da sala.

        Só aprende com blocos que **não** estão acima do limiar (ou seja, que
        não parecem fala): assim a sua voz nunca inflaciona o piso e deixa o
        Jarvis surdo — cuidado que aparece em quem dita texto longo ou fala
        sem pausas. Ruído constante que fica *acima* do limiar é tratado pelo
        vigia em :meth:`_vigia_de_ruido_continuo`.
        """

        if acima_do_limiar:
            return

        # Nos primeiros blocos a estimativa é rápida: se a sala já está
        # barulhenta quando o Jarvis abre, o piso acompanha de imediato.
        if self._contador <= self.blocos_de_estimativa_inicial:
            taxa = 0.5
        else:
            taxa = 0.25

        self.piso_ruido += taxa * (nivel - self.piso_ruido)
        self.piso_ruido = float(
            np.clip(self.piso_ruido, self.piso_ruido_minimo, self.piso_ruido_maximo)
        )

    def _calcular_limiar(self) -> float:
        """Limiar com teto: nunca fica tão alto a ponto de a voz não passar."""

        return float(
            min(
                max(self.limiar_minimo, self.piso_ruido * self.multiplicador_ruido),
                self.teto_limiar,
            )
        )

    def _variacao_recente(self) -> float:
        """Variação (desvio/média) dos níveis acima do limiar.

        Fala modula entre sílabas (variação alta); ventilador e chiado são
        quase constantes (variação baixa). É assim que o detector decide se
        aquilo que está soando é você ou é o ambiente.
        """

        if len(self._niveis_acima) < 15:
            return 1.0

        niveis = np.asarray(self._niveis_acima, dtype=float)
        media = float(niveis.mean())
        return float(niveis.std() / max(media, 1.0))

    # -- núcleo ------------------------------------------------------------

    def processar(self, bloco: np.ndarray, agora: float | None = None) -> np.ndarray | None:
        """Processa um bloco. Devolve a frase completa quando ela termina."""

        if bloco is None or len(bloco) == 0:
            return None

        if bloco.ndim > 1:
            bloco = bloco[:, 0]

        self._contador += 1

        if agora is None:
            agora = self._contador * DURACAO_BLOCO

        original = np.asarray(bloco).astype(np.int16, copy=True)
        amostras = original.astype(np.float32)

        filtrado = self._filtrar(amostras)
        nivel = float(np.sqrt(np.mean(filtrado * filtrado))) if filtrado.size else 0.0

        self.limiar_atual = self._calcular_limiar()
        self.nivel_atual = nivel

        acima = nivel > self.limiar_atual
        self._atualizar_piso(nivel, acima)

        if acima:
            self._blocos_acima += 1
            self._ultimo_bloco_acima = agora
            self._niveis_acima.append(nivel)

            if self._inicio_acima_continuo == 0.0:
                self._inicio_acima_continuo = agora
                self._minimo_acima = nivel
            else:
                self._minimo_acima = min(self._minimo_acima, nivel)
        else:
            self._blocos_acima = 0
            self._niveis_acima.clear()
            self._inicio_acima_continuo = 0.0
            self._minimo_acima = float("inf")

        if self._vigia_de_ruido_continuo(agora, nivel):
            return None

        if not self._falando:
            self._anteriores.append(original)

            if self._blocos_acima >= self.blocos_para_iniciar:
                self._falando = True
                self._inicio_fala = agora
                self._ultimo_bloco_acima = agora
                self._gravados = list(self._anteriores)
                self._anteriores.clear()
                self.ultimo_motivo = "gravando"
            else:
                self.ultimo_motivo = "aguardando voz"

            return None

        self._gravados.append(original)

        terminou_por_silencio = (
            agora - self._ultimo_bloco_acima >= self.silencio_para_encerrar
            and agora - self._inicio_fala >= 0.4
        )
        terminou_por_tempo = agora - self._inicio_fala >= self.tempo_maximo_fala

        if not (terminou_por_silencio or terminou_por_tempo):
            return None

        # Corta o silêncio final: mantém só uma pequena cauda depois da última
        # parte com voz. Áudio menor = reconhecimento mais rápido e menos
        # alucinação do transcritor em cima de silêncio.
        blocos_de_silencio = int(round((agora - self._ultimo_bloco_acima) / DURACAO_BLOCO))
        margem = int(round(0.25 / DURACAO_BLOCO))
        cortar = min(
            max(0, blocos_de_silencio - margem),
            max(0, len(self._gravados) - 1),
        )
        if cortar:
            self._gravados = (
                self._gravados[:-cortar] if cortar < len(self._gravados) else self._gravados[:1]
            )

        frase = np.concatenate(self._gravados) if self._gravados else None
        duracao_de_fala = self._ultimo_bloco_acima - self._inicio_fala

        self._falando = False
        self._gravados = []
        self._blocos_acima = 0
        self._anteriores.clear()

        if frase is None or duracao_de_fala < self.tempo_minimo_fala:
            self._blocos_descartados_curtos += 1
            self.ultimo_motivo = "trecho curto descartado"
            return None

        self.ultimo_motivo = "frase capturada"
        return frase

    def _vigia_de_ruido_continuo(self, agora: float, nivel: float) -> bool:
        """Detecta ruído constante disfarçado de fala. Devolve True se agiu."""

        if not self._inicio_acima_continuo:
            return False

        tempo_no_inicio = self._contador * DURACAO_BLOCO <= self.segundos_de_aquecimento
        janela = (
            self.tempo_maximo_ruido_continuo_no_inicio
            if tempo_no_inicio
            else self.tempo_maximo_ruido_continuo
        )

        if agora - self._inicio_acima_continuo < janela:
            return False

        # O nível mais baixo do período contínuo é o melhor palpite do fundo
        # real da sala. Corrigir o piso por ele resolve o caso mais chato: um
        # ventilador que fica *logo acima* do limiar inicial e mantém o
        # detector travado (a voz continua passando, porque o limiar sobe para
        # 1,8x o fundo e a fala é bem maior que isso).
        if self._minimo_acima != float("inf"):
            self.piso_ruido = float(
                np.clip(
                    max(self.piso_ruido, self._minimo_acima),
                    self.piso_ruido_minimo,
                    self.piso_ruido_maximo,
                )
            )
            self.limiar_atual = self._calcular_limiar()

        if self._variacao_recente() > self.variacao_minima_para_fala:
            # Tem modulação de sílabas: é fala longa, não ruído. O vigia
            # recomeça a contagem em vez de cortar o que a pessoa diz.
            self._inicio_acima_continuo = agora
            return False

        # Ruído constante: força o piso para o nível observado e descarta a
        # gravação em andamento. É o que devolve a audição em ambiente
        # barulhento em vez de ficar travado.
        self.piso_ruido = float(
            np.clip(max(self.piso_ruido, nivel), self.piso_ruido_minimo, self.piso_ruido_maximo)
        )
        self.limiar_atual = self._calcular_limiar()
        self._blocos_acima = 0
        self._inicio_acima_continuo = agora
        self._niveis_acima.clear()
        self._anteriores.clear()
        self._gravados = []
        self._falando = False
        self._recalibracoes += 1
        self.ultimo_motivo = "ruído constante: recalibrado"
        return True

    def reiniciar(self) -> None:
        """Descarta o que está em andamento (ex.: enquanto a Jarvis fala)."""

        self._falando = False
        self._gravados = []
        self._anteriores.clear()
        self._blocos_acima = 0
        self._inicio_acima_continuo = 0.0
        self._minimo_acima = float("inf")
        self._niveis_acima.clear()
        self._filtro.reiniciar()
        self.ultimo_motivo = "reiniciado"

    def estatisticas(self) -> dict:
        """Números para o painel explicar *por que* está (ou não) ouvindo."""

        return {
            "nivel": round(self.nivel_atual, 1),
            "piso_ruido": round(self.piso_ruido, 1),
            "limiar": round(self.limiar_atual, 1),
            "relacao": round(self.nivel_atual / max(self.limiar_atual, 1.0), 2),
            "falando": self._falando,
            "motivo": self.ultimo_motivo,
            "trechos_curtos_descartados": self._blocos_descartados_curtos,
            "recalibracoes_de_ruido": self._recalibracoes,
        }


# ---------------------------------------------------------------------------
# Microfone
# ---------------------------------------------------------------------------


def listar_microfones() -> list[dict]:
    """Lista apenas dispositivos que realmente captam áudio.

    O código antigo escolhia pelo nome e nunca verificava ``max_input_channels``
    — no Windows o mesmo aparelho aparece como entrada e saída com o mesmo
    nome, então um dispositivo só de saída podia ser escolhido e o microfone
    nunca funcionava.
    """

    try:
        import sounddevice as sd

        dispositivos = sd.query_devices()
        padrao_entrada = None
        try:
            padrao_entrada = int(sd.default.device[0])
        except Exception:
            padrao_entrada = None

        encontrados = []
        for indice, item in enumerate(dispositivos):
            canais = int(item.get("max_input_channels", 0) or 0)
            if canais <= 0:
                continue
            encontrados.append(
                {
                    "indice": indice,
                    "nome": str(item.get("name", "")).strip(),
                    "canais": canais,
                    "padrao": indice == padrao_entrada,
                }
            )
        return encontrados
    except Exception:
        return []


def escolher_microfone(config: dict | None = None) -> tuple[int | None, str]:
    """Escolhe o microfone e devolve ``(indice, nome)``.

    Ordem de preferência:
    1. o que estiver salvo na configuração (``microfone``: índice ou parte do nome);
    2. o dispositivo padrão de entrada do sistema;
    3. um aparelho de entrada com "mic"/"microfone"/"array" no nome;
    4. qualquer aparelho de entrada.
    """

    config = config or {}
    preferido = config.get("microfone")

    try:
        import sounddevice as sd
    except Exception as erro:
        return None, f"sounddevice indisponível ({erro})"

    dispositivos = listar_microfones()

    if preferido not in (None, ""):
        texto = str(preferido).strip()

        for item in dispositivos:
            if texto.isdigit() and item["indice"] == int(texto):
                return item["indice"], item["nome"]

        for item in dispositivos:
            if texto.lower() in item["nome"].lower():
                return item["indice"], item["nome"]

    for item in dispositivos:
        if item["padrao"]:
            return item["indice"], item["nome"]

    palavras = ("microfone", "microphone", "mic", "array")
    rejeitadas = ("mapeador", "mapper", "output", "speaker", "alto-falante", "loopback")

    for item in dispositivos:
        nome = item["nome"].lower()
        if any(p in nome for p in palavras) and not any(r in nome for r in rejeitadas):
            return item["indice"], item["nome"]

    if dispositivos:
        return dispositivos[0]["indice"], dispositivos[0]["nome"]

    # Nenhum aparelho de entrada encontrado: devolve None e deixa o PortAudio
    # usar o padrão. Antes o código podia devolver um índice de saída e o
    # microfone nunca abria.
    return None, ""


# ---------------------------------------------------------------------------
# Ouvinte (thread)
# ---------------------------------------------------------------------------


class ThreadOuvinte:
    """Mantém o microfone aberto e entrega frases prontas numa fila.

    Roda fora do laço principal, então o Jarvis continua respondendo à
    interface enquanto escuta — e pode ser pausado enquanto fala.
    """

    def __init__(
        self,
        config: dict | None = None,
        detector: DetectorDeVoz | None = None,
        logger: logging.Logger | None = None,
        tamanho_fila: int = 8,
    ):
        self.logger = logger or logging.getLogger("jarvis.audio")
        self.config = config or {}
        self.detector = detector or DetectorDeVoz()
        self.fila: queue.Queue = queue.Queue(maxsize=tamanho_fila)
        self.fila_bruta: queue.Queue = queue.Queue(maxsize=200)

        self.escolhido: int | None = None
        self.nome_microfone = ""
        self.ultimo_erro = ""
        self.ativo = False
        self.pausado = False

        self._stream = None
        self._worker: threading.Thread | None = None
        self._parar = threading.Event()
        self._lock = threading.Lock()

    # -- ciclo de vida -----------------------------------------------------

    def _callback(self, indata, frames, tempo, status):
        if status and self.logger:
            self.logger.debug("Status do áudio: %s", status)

        try:
            self.fila_bruta.put_nowait(indata.copy())
        except queue.Full:
            pass

    def iniciar(self) -> bool:
        """Abre o microfone. Devolve False e explica o erro se não conseguir."""

        try:
            import sounddevice as sd
        except Exception as erro:
            self.ultimo_erro = f"sounddevice não instalado ({erro})"
            self.logger.error(self.ultimo_erro)
            return False

        if self._stream is not None:
            return True

        self.escolhido, self.nome_microfone = escolher_microfone(self.config)
        self.logger.info(
            "Microfone escolhido: %s (índice %s)",
            self.nome_microfone or "padrão do sistema",
            self.escolhido,
        )

        parametros = {
            "samplerate": self.detector.taxa,
            "channels": 1,
            "dtype": "int16",
            "blocksize": BLOCOSIZE,
            "callback": self._callback,
        }
        if self.escolhido is not None:
            parametros["device"] = self.escolhido

        try:
            self._stream = sd.InputStream(**parametros)
            self._stream.start()
        except Exception as erro:
            self._stream = None
            self.ultimo_erro = str(erro)
            self.logger.error("Não foi possível abrir o microfone: %s", erro)
            return False

        self._parar.clear()
        self._worker = threading.Thread(
            target=self._processar_em_thread, name="voz", daemon=True
        )
        self._worker.start()
        self.ativo = True
        self.ultimo_erro = ""
        return True

    def parar(self) -> None:
        self._parar.set()

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=1.0)

        self._worker = None
        self.ativo = False

    def pausar(self) -> None:
        """Deixa de capturar (usado enquanto a Jarvis está falando)."""

        self.pausado = True
        self.detector.reiniciar()

    def retomar(self) -> None:
        self.pausado = False
        self.detector.reiniciar()

    def garantir_ativo(self) -> bool:
        """Reabre o microfone se ele caiu (ex.: aparelho desconectado)."""

        if self._stream is not None and self.ativo:
            return True

        self.parar()
        return self.iniciar()

    # -- processamento -----------------------------------------------------

    def _processar_em_thread(self) -> None:
        while not self._parar.is_set():
            try:
                bloco = self.fila_bruta.get(timeout=0.2)
            except queue.Empty:
                continue

            if self.pausado:
                continue

            try:
                frase = self.detector.processar(bloco)
            except Exception:
                self.logger.exception("Erro ao processar áudio")
                continue

            if frase is None:
                continue

            try:
                self.fila.put_nowait(frase)
            except queue.Full:
                self.logger.warning("Fila de falas cheia; descartando frase")

    def retirar_frase(self, timeout: float = 0.0):
        """Retira uma frase pronta, ou ``None`` se não houver."""

        try:
            return self.fila.get(timeout=timeout) if timeout > 0 else self.fila.get_nowait()
        except queue.Empty:
            return None

    def estatisticas(self) -> dict:
        dados = self.detector.estatisticas()
        dados.update(
            {
                "microfone": self.nome_microfone,
                "microfone_indice": self.escolhido,
                "ativo": self.ativo,
                "pausado": self.pausado,
                "erro": self.ultimo_erro,
            }
        )
        return dados


def tempo_agora() -> float:
    return time.monotonic()
