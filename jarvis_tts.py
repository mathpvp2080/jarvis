"""Fala com processo persistente — o fim do "um PowerShell por frase".

Problema que isto resolve (B10)
-------------------------------
Antes, **cada** frase da Jarvis abria um `powershell.exe` novo para construir
um `SpeechSynthesizer`, selecionar a voz e falar. Isso custa centenas de
milissegundos a mais de 1 segundo *por fala*, e a cada fala um processo novo
aparecia no computador. Também não era possível interromper a Jarvis: o
processo não tinha como receber um "pare".

Como funciona agora
-------------------
Um único processo PowerShell fica de pé, com o sintetizador já pronto, e
conversa com o Python por linhas de texto:

* o Python manda o texto em **base64** (uma linha) — nada de escapar aspas;
* o processo responde ``!falando`` e ``!livre``, então o Python sabe quando a
  Jarvis está falando de verdade;
* ``!parar`` interrompe a fala atual (permite falar por cima);
* ``!sair`` encerra o processo.

Se o processo morrer (ou fora do Windows), :meth:`MotorDeFala.falar` devolve
``False`` e quem chamou decide o que fazer — em ``main.py`` existe o caminho
antigo como reserva.
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import threading
import time

CRIAR_SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Marca que o PowerShell usa para dizer que está falando / livre.
MARCA_FALANDO = "!falando"
MARCA_LIVRE = "!livre"

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'

try {
    Add-Type -AssemblyName System.Speech
} catch {
    [Console]::Error.WriteLine("System.Speech indisponivel: $_")
    exit 3
}

$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = 1
$s.Volume = 100
__SOBRE_A_VOZ__

$pendentes = New-Object System.Collections.Queue
$falando = $false

function Anunciar($marca) {
    [Console]::Out.WriteLine($marca)
    [Console]::Out.Flush()
}

while ($true) {
    if ($falando -and $s.State -eq [System.Speech.Synthesis.SynthesizerState]::Ready) {
        $falando = $false
        Anunciar('!livre')
    }

    if ($falando) {
        if ([Console]::In.Peek() -ge 0) {
            $linha = [Console]::In.ReadLine()
            if ($linha -eq '!parar') { $s.SpeakAsyncCancelAll() }
            elseif ($linha -eq '!sair') { $s.SpeakAsyncCancelAll(); break }
            elseif ($linha) { $pendentes.Enqueue($linha) }
        }
        Start-Sleep -Milliseconds 40
        continue
    }

    if ($pendentes.Count -gt 0) {
        $linha = $pendentes.Dequeue()
        $texto = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($linha))
        $s.SpeakAsync($texto)
        $falando = $true
        Anunciar('!falando')
        continue
    }

    $linha = [Console]::In.ReadLine()
    if ($null -eq $linha) { break }
    if ($linha -eq '!sair') { break }
    if ($linha -eq '!parar') { continue }
    if ($linha) { $pendentes.Enqueue($linha) }
}
"""


def _codificar(texto: str) -> str:
    return base64.b64encode(str(texto).encode("utf-8")).decode("ascii")


class MotorDeFala:
    """Sintetizador persistente (Windows) com interrupção."""

    def __init__(self, logger: logging.Logger | None = None, voz: str = "", taxa: int = 1):
        self.log = logger or logging.getLogger("jarvis.tts")
        self.voz = voz or ""
        self.taxa = taxa
        self._processo: subprocess.Popen | None = None
        self._trava = threading.Lock()
        self._falando = threading.Event()
        self._leitor: threading.Thread | None = None
        self._ultimo_erro = ""
        self._frases = 0

    # -- estado ------------------------------------------------------------

    @property
    def disponivel(self) -> bool:
        return os.name == "nt"

    @property
    def ultimo_erro(self) -> str:
        return self._ultimo_erro

    @property
    def frases_faladas(self) -> int:
        return self._frases

    def falando(self) -> bool:
        return self._falando.is_set()

    def ativo(self) -> bool:
        processo = self._processo
        return processo is not None and processo.poll() is None

    # -- ciclo de vida -----------------------------------------------------

    def _script(self) -> str:
        if self.voz:
            nome = self.voz.replace("'", "''")
            sobre = f"try {{ $s.SelectVoice('{nome}') }} catch {{ }}"
        else:
            sobre = "# sem voz escolhida: usa a padrão do sistema"

        return _SCRIPT.replace("__SOBRE_A_VOZ__", sobre)

    def iniciar(self) -> bool:
        """Sobe o processo de fala. Devolve True se ficou pronto."""

        with self._trava:
            if self.ativo():
                return True

        if not self.disponivel:
            self._ultimo_erro = "fala contínua só existe no Windows"
            return False

            script = self._script()
            codificado = base64.b64encode(script.encode("utf-16le")).decode("ascii")

            try:
                self._processo = subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-NoLogo", "-EncodedCommand", codificado],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=CRIAR_SEM_JANELA,
                )
            except (FileNotFoundError, OSError) as erro:
                self._ultimo_erro = f"não consegui abrir o PowerShell: {erro}"
                self.log.error(self._ultimo_erro)
                self._processo = None
                return False

            self._leitor = threading.Thread(
                target=self._ler_saida, name="jarvis-tts", daemon=True
            )
            self._leitor.start()

            # O processo avisa que está pronto lendo o stdin; se System.Speech
            # não existir, ele morre logo — aí perdemos o tempo do usuário.
            time.sleep(0.15)

            if self._processo.poll() is not None:
                self._ultimo_erro = self._ler_erro() or "o processo de fala encerrou ao iniciar"
                self.log.error("Processo de fala não subiu: %s", self._ultimo_erro)
                self._processo = None
                return False

            self.log.info("Motor de fala persistente iniciado (voz=%r)", self.voz or "padrão")
            return True

    def _ler_erro(self) -> str:
        processo = self._processo

        if processo is None or processo.stderr is None:
            return ""

        try:
            return (processo.stderr.read() or b"").decode("utf-8", "replace").strip()
        except (OSError, ValueError):
            return ""

    def _ler_saida(self) -> None:
        processo = self._processo

        if processo is None or processo.stdout is None:
            return

        try:
            for linha in processo.stdout:
                marca = linha.decode("utf-8", "replace").strip()

                if marca == MARCA_FALANDO:
                    self._falando.set()
                elif marca == MARCA_LIVRE:
                    self._falando.clear()
        except (OSError, ValueError):
            pass
        finally:
            self._falando.clear()

    def fechar(self) -> None:
        with self._trava:
            processo = self._processo

            if processo is None:
                return

            try:
                if processo.stdin and not processo.stdin.closed:
                    processo.stdin.write(b"!sair\n")
                    processo.stdin.flush()
            except (OSError, ValueError):
                pass

            try:
                processo.wait(timeout=3)
            except subprocess.TimeoutExpired:
                processo.kill()

            self._processo = None
            self._falando.clear()

    # -- fala --------------------------------------------------------------

    def falar(self, texto: str, esperar: bool = False, tempo_maximo: float = 60.0) -> bool:
        """Manda uma frase para falar. Devolve False se o motor está fora."""

        texto = str(texto or "").strip()

        if not texto:
            return True

        if not self.iniciar():
            return False

        with self._trava:
            processo = self._processo

            if processo is None or processo.stdin is None or processo.poll() is not None:
                self._ultimo_erro = "processo de fala indisponível"
                return False

            try:
                processo.stdin.write((_codificar(texto) + "\n").encode("ascii"))
                processo.stdin.flush()
                self._frases += 1
            except (BrokenPipeError, OSError, ValueError) as erro:
                self._ultimo_erro = f"não consegui enviar o texto: {erro}"
                self.log.error(self._ultimo_erro)
                self._processo = None
                return False

        if esperar:
            limite = time.monotonic() + tempo_maximo

            # Primeiro espera começar (senão "terminou" é verdade cedo demais).
            while time.monotonic() < limite and not self.falando():
                if not self.ativo():
                    return False
                time.sleep(0.02)

            while time.monotonic() < limite and self.falando():
                if not self.ativo():
                    return False
                time.sleep(0.02)

        return True

    def parar(self) -> bool:
        """Interrompe a fala atual (usado quando você começa a falar)."""

        with self._trava:
            processo = self._processo

            if processo is None or processo.stdin is None or processo.poll() is not None:
                return False

            try:
                processo.stdin.write(b"!parar\n")
                processo.stdin.flush()
                self._falando.clear()
                return True
            except (BrokenPipeError, OSError, ValueError):
                return False
