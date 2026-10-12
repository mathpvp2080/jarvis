"""Reconhecimento de voz com mais de um motor (plano B de verdade).

Problema que este módulo resolve
--------------------------------
O ``main.py`` original tinha um único motor: o reconhecimento do Google, que
depende de internet e de um serviço gratuito sujeito a bloqueio. Quando ele
falhava, o Jarvis ficava surdo e repetia "Erro ao acessar o reconhecimento de
voz" a cada tentativa — foram **81 mensagens iguais em um único dia**
(21/08/2026) no histórico.

Aqui existe uma cadeia:

1. ``google``  — o motor já usado (rápido, preciso, precisa de internet);
2. ``whisper`` — ``faster-whisper`` local, se instalado (roda offline depois
   de baixar o modelo uma vez);
3. ``vosk``    — ``vosk`` local + modelo em português, se instalado;
4. ``windows`` — reconhecimento nativo do Windows (``System.Speech``), sem
   instalar nada além do pacote de idioma.

A escolha é automática: se o motor preferido falhar por rede/rede indisponível,
o próximo entra em campo; e depois de N falhas seguidas o Jarvis passa a usar
o motor offline durante alguns minutos. Avisos ao usuário são limitados a um
por minuto (nada de repetir o mesmo erro em loop).
"""

from __future__ import annotations

import base64
import importlib.util
import json
import logging
import os
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

TAXA_DE_AUDIO = 16000
LIMITE_DE_FALHAS_PARA_TROCAR = 3
SEGUNDOS_EM_MODO_OFFLINE = 300.0
INTERVALO_MINIMO_ENTRE_AVISOS = 60.0

MOTORES_OFFLINE = ("whisper", "vosk", "windows")

PALAVRAS_PRIORITARIAS = (
    "jarvis", "jarves", "jarviz", "jarvi", "javis",
    "tocar", "toque", "colocar", "coloque", "abrir",
    "pesquisar", "pesquise", "procure", "buscar", "corrigir",
)


def normalizar(texto: str) -> str:
    import unicodedata

    texto = unicodedata.normalize("NFD", texto.lower())
    texto = "".join(letra for letra in texto if unicodedata.category(letra) != "Mn")
    return " ".join(texto.split())


@dataclass
class ResultadoDeVoz:
    """Resultado de uma tentativa de transcrição."""

    texto: str = ""
    motor: str = ""
    falhou: bool = False
    falha_de_rede: bool = False
    motivo: str = ""
    motores_tentados: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.texto) and not self.falhou


# ---------------------------------------------------------------------------
# Motores
# ---------------------------------------------------------------------------


def motor_whisper_disponivel() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def motor_vosk_disponivel(pasta_modelo: str | None = None) -> bool:
    if importlib.util.find_spec("vosk") is None:
        return False
    return bool(pasta_modelo and Path(pasta_modelo).exists())


def motor_windows_disponivel() -> bool:
    return os.name == "nt" and bool(_caminho_powershell())


def _caminho_powershell() -> str:
    import shutil

    return shutil.which("powershell.exe") or shutil.which("powershell") or ""


class MotoresDeVoz:
    """Implementações concretas. A câmera de testes troca estes métodos."""

    def __init__(self, config: dict | None = None, logger: logging.Logger | None = None):
        self.config = config or {}
        self.logger = logger or logging.getLogger("jarvis.voz")
        self._modelo_whisper = None
        self._modelo_vosk = None

    # -- google ------------------------------------------------------------

    def google(self, audio: np.ndarray) -> str:
        import speech_recognition as sr

        reconhecedor = sr.Recognizer()
        reconhecedor.pause_threshold = 1.2
        reconhecedor.non_speaking_duration = 0.4

        dados = sr.AudioData(audio.astype(np.int16).tobytes(), TAXA_DE_AUDIO, 2)
        resultado = reconhecedor.recognize_google(
            dados, language="pt-BR", show_all=True
        )

        if not resultado or not resultado.get("alternative"):
            raise ValueError("nenhuma alternativa de transcrição")

        candidatos = [
            item.get("transcript", "").strip().lower()
            for item in resultado["alternative"]
            if item.get("transcript", "").strip()
        ]

        if not candidatos:
            raise ValueError("transcrição vazia")

        candidatos.sort(
            key=lambda texto: sum(
                palavra in normalizar(texto) for palavra in PALAVRAS_PRIORITARIAS
            ),
            reverse=True,
        )
        return candidatos[0]

    # -- whisper (faster-whisper) -----------------------------------------

    def whisper(self, audio: np.ndarray) -> str:
        from faster_whisper import WhisperModel

        if self._modelo_whisper is None:
            nome = self.config.get("modelo_whisper", "small")
            self.logger.info("Carregando modelo Whisper '%s' (primeira vez pode demorar)", nome)
            self._modelo_whisper = WhisperModel(nome, device="cpu", compute_type="int8")

        amostras = audio.astype(np.float32) / 32768.0
        segmentos, _ = self._modelo_whisper.transcribe(
            amostras, language="pt", beam_size=1
        )
        return " ".join(segmento.text.strip() for segmento in segmentos).strip()

    # -- vosk --------------------------------------------------------------

    def vosk(self, audio: np.ndarray) -> str:
        from vosk import KaldiRecognizer, Model

        if self._modelo_vosk is None:
            pasta = str(self.config.get("pasta_modelo_vosk", "")).strip()
            if not pasta or not Path(pasta).exists():
                raise RuntimeError("pasta do modelo Vosk não configurada")
            self._modelo_vosk = Model(pasta)

        reconhecedor = KaldiRecognizer(self._modelo_vosk, TAXA_DE_AUDIO)
        reconhecedor.AcceptWaveform(audio.astype(np.int16).tobytes())
        return json.loads(reconhecedor.FinalResult()).get("text", "").strip()

    # -- reconhecimento nativo do Windows ---------------------------------

    def windows(self, audio: np.ndarray) -> str:
        if os.name != "nt":
            raise RuntimeError("reconhecimento nativo só existe no Windows")

        executavel = _caminho_powershell()
        if not executavel:
            raise RuntimeError("PowerShell não encontrado")

        arquivo = Path(tempfile.gettempdir()) / f"jarvis_voz_{os.getpid()}.wav"
        with wave.open(str(arquivo), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(TAXA_DE_AUDIO)
            wav.writeframes(audio.astype(np.int16).tobytes())

        comando = (
            "Add-Type -AssemblyName System.Speech; "
            "$r = New-Object System.Speech.Recognition.SpeechRecognitionEngine; "
            f"$r.SetInputToWaveFile('{arquivo}'); "
            "$r.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar)); "
            "$t = $r.Recognize(); "
            "if ($t) { [Console]::Out.WriteLine($t.Text) }"
        )
        codificado = base64.b64encode(comando.encode("utf-16le")).decode("ascii")

        try:
            processo = subprocess.run(
                [executavel, "-NoProfile", "-EncodedCommand", codificado],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=25,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        finally:
            try:
                arquivo.unlink(missing_ok=True)
            except OSError:
                pass

        texto = (processo.stdout or "").strip()
        if processo.returncode != 0 or not texto:
            raise RuntimeError(
                f"reconhecimento do Windows não devolveu texto (código {processo.returncode})"
            )
        return texto.lower()


# ---------------------------------------------------------------------------
# Gerenciador
# ---------------------------------------------------------------------------


class GerenciadorDeVoz:
    """Escolhe o motor, tenta o plano B e limita os avisos de erro."""

    def __init__(
        self,
        config: dict | None = None,
        logger: logging.Logger | None = None,
        motores=None,
    ):
        self.config = config or {}
        self.logger = logger or logging.getLogger("jarvis.voz")
        self.motores = motores or MotoresDeVoz(self.config, self.logger)

        self.falhas_seguidas = 0
        self.modo_offline_ate = 0.0
        # None (e não 0.0) porque perto do boot time.monotonic() ainda é menor
        # que INTERVALO_MINIMO_ENTRE_AVISOS: com 0.0 o primeiro aviso de erro
        # era engolido e o usuário ficava sem saber que a voz falhou.
        self.ultimo_aviso: float | None = None
        self.ultimo_motor = ""

    # -- disponibilidade ---------------------------------------------------

    def disponiveis(self) -> list[str]:
        lista = ["google"]

        if motor_whisper_disponivel():
            lista.append("whisper")
        if motor_vosk_disponivel(self.config.get("pasta_modelo_vosk")):
            lista.append("vosk")
        if motor_windows_disponivel():
            lista.append("windows")

        return lista

    def _ordem_de_motores(self) -> list[str]:
        """Ordem de tentativa: preferido -> padrão -> offline."""

        disponiveis = self.disponiveis()
        preferido = str(self.config.get("motor_de_voz", "") or "").strip().lower()

        if preferido and preferido in disponiveis:
            ordem = [preferido] + [m for m in disponiveis if m != preferido]
        else:
            ordem = list(disponiveis)

        # Depois de várias falhas, prioriza um motor que não depende de rede.
        if time.monotonic() < self.modo_offline_ate:
            offline = [m for m in ordem if m in MOTORES_OFFLINE]
            if offline:
                ordem = offline + [m for m in ordem if m not in offline]

        return ordem

    def em_modo_offline(self) -> bool:
        return time.monotonic() < self.modo_offline_ate

    # -- transcrição -------------------------------------------------------

    def transcrever(self, audio: np.ndarray) -> ResultadoDeVoz:
        if audio is None or len(audio) == 0:
            return ResultadoDeVoz(falhou=True, motivo="áudio vazio")

        resultado = ResultadoDeVoz()
        falhou_por_rede = False

        for motor in self._ordem_de_motores():
            resultado.motores_tentados.append(motor)
            metodo = getattr(self.motores, motor, None)

            if metodo is None:
                continue

            try:
                texto = str(metodo(audio) or "").strip()
            except Exception as erro:
                mensagem = f"{type(erro).__name__}: {erro}"
                self.logger.warning("Motor de voz '%s' falhou: %s", motor, mensagem)
                resultado.motivo = mensagem

                if _parece_falha_de_rede(erro, mensagem):
                    falhou_por_rede = True
                continue

            if not texto:
                self.logger.info("Motor de voz '%s' não entendeu a fala", motor)
                resultado.motivo = "não entendeu a fala"
                continue

            resultado.texto = texto
            resultado.motor = motor
            resultado.falha_de_rede = False
            self.ultimo_motor = motor
            self.falhas_seguidas = 0
            return resultado

        # Nenhum motor conseguiu.
        resultado.falhou = True
        resultado.falha_de_rede = falhou_por_rede
        self.falhas_seguidas += 1

        if self.falhas_seguidas >= LIMITE_DE_FALHAS_PARA_TROCAR and any(
            m in self.disponiveis() for m in MOTORES_OFFLINE
        ):
            self.modo_offline_ate = time.monotonic() + SEGUNDOS_EM_MODO_OFFLINE
            self.logger.warning(
                "Muitas falhas seguidas (%s): usando motores offline por %.0f minutos",
                self.falhas_seguidas,
                SEGUNDOS_EM_MODO_OFFLINE / 60.0,
            )

        return resultado

    # -- avisos ao usuário -------------------------------------------------

    def mensagem_para_usuario(self, resultado: ResultadoDeVoz) -> str:
        """Mensagem curta e honesta, ou vazio se for melhor não falar nada."""

        if resultado.ok:
            return ""

        if resultado.falha_de_rede:
            return (
                "Não consegui usar o reconhecimento pela internet. "
                "Confira a conexão ou instale um motor de voz offline nas configurações."
            )

        if not resultado.motivo:
            return "Não consegui entender. Tente falar novamente."

        return "Não consegui entender o que foi dito. Tente novamente."

    def deve_avisar_agora(self) -> bool:
        """Evita repetir o mesmo aviso em loop (era o que gerava 81 mensagens)."""

        agora = time.monotonic()

        if self.ultimo_aviso is not None:
            if agora - self.ultimo_aviso < INTERVALO_MINIMO_ENTRE_AVISOS:
                return False

        self.ultimo_aviso = agora
        return True


def _parece_falha_de_rede(erro: Exception, mensagem: str) -> bool:
    """Distingue 'não entendi' de 'não consegui falar com o serviço'."""

    if type(erro).__name__ in ("RequestError", "ConnectionError", "TimeoutError"):
        return True

    marcadores = (
        "connection", "network", "timed out", "timeout", "unreachable",
        "getaddrinfo", "ssl", "recv", "http", "429", "403", "unavailable",
    )
    return any(marcador in mensagem.lower() for marcador in marcadores)
