import atexit
import base64
import ctypes
import difflib
import json
import os
import shutil
import subprocess
import time

from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

import mss
import pyautogui
import pyperclip
import pytesseract

from PIL import Image

from jarvis_audio import DetectorDeVoz, ThreadOuvinte
from jarvis_agente import Agente
from jarvis_comum import (
    anexar_jsonl,
    configurar_log,
    consumir_comandos,
    escrever_json_atomico,
    ler_json,
    ler_jsonl,
    publicar_evento,
    registrar_excecoes_nao_tratadas,
)
from jarvis_intencoes import (
    ENCHIMENTO_DE_ARGUMENTO,
    EQUIVALENTES_DE_VERBO,
    LIXO_INICIAL_DO_ARGUMENTO,
    PALAVRAS_FORA_DO_ARGUMENTO,
    Intencao,
    descrever as descrever_intencao,
    extrair_argumento,
    normalizar,
    quer_ligar,
    reconhecer,
)
from jarvis_tts import MotorDeFala
from jarvis_ferramentas import (
    Ferramenta,
    RegistroDeFerramentas,
    executar_ferramenta,
)
from jarvis_voz import GerenciadorDeVoz

log = configurar_log("jarvis")
registrar_excecoes_nao_tratadas(log)

# `queue` e `speech_recognition` deixaram de ser usados aqui: a captura de
# áudio passou para jarvis_audio (com thread própria) e o reconhecimento
# para jarvis_voz (com motores de reserva). O selenium saiu junto com a
# função de automação que ninguém chamava.

CRIAR_SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


if os.name == "nt":
    # Trava de instância única (só faz sentido no Windows).
    ctypes.windll.kernel32.CreateMutexW(None, False, "JarvisAssistenteDeVozUnica")

    if ctypes.windll.kernel32.GetLastError() == 183:
        print("A Jarvis já está aberta. Feche a outra janela antes de iniciar novamente.")
        raise SystemExit


# Parâmetros de áudio de reserva: o detector em jarvis_audio tem os seus
# próprios (e adaptativos). Estes são usados só em `criar_detector_de_voz`,
# que traduz a configuração do painel para o detector.
SESSAO_CONVERSA = 30
TEMPO_COOLDOWN_FALA = 0.8
TEMPO_DE_ESPERA_POR_RESPOSTA = 8.0
INTERVALO_DE_STATUS = 2.0

PASTA = Path(__file__).resolve().parent
ARQUIVO_MEMORIA = PASTA / "memoria_jarvis.jsonl"
ARQUIVO_MEMORIA_LEGADO = PASTA / "memoria_jarvis.json"

# Quantos registros ficam na memória do processo (a conversa recente basta) e
# a partir de que tamanho o arquivo do histórico é trocado.
MEMORIAS_EM_RAM = 2000
ARQUIVO_CONFIG = PASTA / "config_jarvis.json"
ARQUIVO_COMANDOS_APRENDIDOS = PASTA / "comandos_aprendidos.json"

CAMINHO_TESSERACT = shutil.which("tesseract") or r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Por quanto tempo a lista de aplicativos do menu Iniciar é reaproveitada.
CACHE_DE_APLICATIVOS = 600.0
_aplicativos_cache: dict = {}
_aplicativos_cache_ate = 0.0

pytesseract.pytesseract.tesseract_cmd = CAMINHO_TESSERACT

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODELO_LOCAL = "qwen2.5:3b"
TEMPO_DE_RESPOSTA_DO_OLLAMA = 60


def localizar_ollama():
    r"""Procura o Ollama de verdade, em vez de confiar num caminho fixo.

    Antes o caminho era fixo em \Users\Matheus\... — se o Ollama estivesse
    em outro lugar (ou a pessoa usasse outro usuário do Windows), toda
    resposta de IA falhava com "verifique se o Ollama está instalado".
    """

    do_ambiente = os.environ.get("OLLAMA_EXECUTABLE", "").strip()
    if do_ambiente and Path(do_ambiente).exists():
        return Path(do_ambiente)

    encontrado = shutil.which("ollama")
    if encontrado:
        return Path(encontrado)

    candidatos = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Ollama" / "ollama.exe",
        Path("/usr/local/bin/ollama"),
        Path("/usr/bin/ollama"),
    ]
    for candidato in candidatos:
        if candidato.exists():
            return candidato

    return None


CAMINHO_OLLAMA = localizar_ollama()

# Preenchido no início do laço (configurar_escalas_de_video).
DPI_DECLARADO = False


def configurar_escalas_de_video():
    """Declara ao Windows que este processo enxerga a escala de vídeo real.

    Por que importa: a captura de tela (mss) e o OCR devolvem coordenadas em
    **pixels físicos**, enquanto o clique do pyautogui usa as coordenadas
    **lógicas** do processo. Com a escala em 125%/150% (padrão em muito
    notebook) os dois sistemas não batem e o "clicar em confirmar" acerta outro
    lugar da tela. Sem declarar nada, o processo fica *unaware* e o Windows
    ainda estica a janela — pior dos dois lados.

    Devolve True quando ficou sabendo da escala (per-pixel ou system), o que já
    basta para as coordenadas casarem.
    """

    if os.name != "nt":
        return False

    for nome in (
        "SetProcessDpiAwarenessContext",  # Windows 10 1703+: -4 = per-monitor v2
        "SetProcessDpiAwareness",        # Windows 8.1+: 2 = por monitor
        "SetProcessDPIAware",            # Windows Vista+: sistema
    ):
        try:
            if nome == "SetProcessDpiAwarenessContext":
                ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            elif nome == "SetProcessDpiAwareness":
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            else:
                ctypes.windll.user32.SetProcessDPIAware()

            log.info("Escala de vídeo declarada ao Windows (%s)", nome)
            return True
        except (AttributeError, OSError):
            continue
        except Exception:
            log.exception("Não consegui declarar a escala de vídeo (%s)", nome)
            continue

    log.warning(
        "Processo sem consciência de DPI: com escala diferente de 100%% o "
        "clique assistido pode acertar outro ponto da tela"
    )
    return False


def escala_de_video():
    """Fator entre pixels físicos e lógicos (1.0 = escala em 100%).

    SM_CXVIRTUALSCREEN (78) e SM_CYVIRTUALSCREEN (79) vêm em pixels físicos;
    a largura/altura "lógicas" do sistema vêm das métricas 0 e 1.
    """

    if os.name != "nt":
        return 1.0

    try:
        usuario32 = ctypes.windll.user32
        fisico = usuario32.GetSystemMetrics(78)
        logico = usuario32.GetSystemMetrics(0)

        if fisico and logico:
            return round(fisico / logico, 4)
    except Exception:
        log.exception("Não consegui medir a escala de vídeo")

    return 1.0


def para_coordenadas_de_clique(x, y, escala=None):
    """Converte um ponto da captura de tela para o sistema do mouse.

    Quando o processo já é *DPI aware* (veja ``configurar_escalas_de_video``),
    a escala é 1.0 e o ponto passa inteiro. Se não deu para declarar, a
    conversão evita que o clique saia pela metade da tela.
    """

    if escala is None:
        escala = escala_de_video()

    if not escala or escala == 1.0:
        return int(x), int(y)

    return int(round(x / escala)), int(round(y / escala))

clique_pendente = None
intencao_pendente = None
processo_fala = None
motor_de_fala = None
tempo_ultima_fala = None
programa_ativo = True
modo_conversa_ate = 0.0
ouvinte = None
gerenciador_voz = None
voz_windows = None
voz_windows_carregada = False
ultimo_status_voz = 0.0


def carregar_config():
    padrao = {
        "visualizacao_de_tela_ativada": False,
        "filtro_palavroes_ativado": False,
        "camera_ativada": True,
        "bloquear_ao_sair": False,
        "detectar_emocoes": True,
        "exoesqueleto_facial": True,
        "holograma_ativado": True,
        "reagir_a_gestos": True,
        # Áudio / voz
        "microfone": "",
        "motor_de_voz": "",
        "modelo_whisper": "small",
        "pasta_modelo_vosk": "",
        "limiar_minimo_de_voz": 45,
        "multiplicador_de_ruido": 1.8,
        "silencio_para_encerrar": 1.0,
        # Cérebro (agente com ferramentas) — veja jarvis_agente.py
        "agente_ativado": True,
        "modelo_local": MODELO_LOCAL,
        "rodadas_do_agente": 4,
    }

    dados = ler_json(ARQUIVO_CONFIG, {})
    if isinstance(dados, dict):
        padrao.update(dados)

    return padrao


def modelo_local():
    """Modelo do Ollama a usar (o `config_jarvis.json` manda)."""

    try:
        return str(carregar_config().get("modelo_local") or MODELO_LOCAL)
    except Exception:
        return MODELO_LOCAL


def agente_ativado():
    """O cérebro com ferramentas está ligado?

    Fica atrás de configuração porque é a mudança mais estrutural do projeto:
    desligar tem que devolver o comportamento antigo, de comando reconhecido,
    sem tocar em mais nada.
    """

    try:
        return bool(carregar_config().get("agente_ativado", True))
    except Exception:
        log.exception("Não consegui ler a configuração do agente")
        return True


def tela_permitida():
    return carregar_config()["visualizacao_de_tela_ativada"]


def filtro_de_palavroes_ativado():
    return carregar_config()["filtro_palavroes_ativado"]


def migrar_memoria_legada():
    """Converte o antigo memoria_jarvis.json (lista) no formato de linhas.

    O arquivo antigo é mantido como estava — serve de cópia de segurança.
    """

    if ARQUIVO_MEMORIA.exists() or not ARQUIVO_MEMORIA_LEGADO.exists():
        return 0

    dados = ler_json(ARQUIVO_MEMORIA_LEGADO, [])

    if not isinstance(dados, list):
        return 0

    quantos = 0

    for item in dados:
        if isinstance(item, dict) and anexar_jsonl(ARQUIVO_MEMORIA, item):
            quantos += 1

    if quantos:
        log.info(
            "Memória convertida para %s (%d registros; o .json antigo ficou de cópia)",
            ARQUIVO_MEMORIA.name,
            quantos,
        )

    return quantos


def carregar_memoria(limite=MEMORIAS_EM_RAM):
    """Lê os registros mais recentes do histórico."""

    return [
        item for item in ler_jsonl(ARQUIVO_MEMORIA, limite)
        if isinstance(item, dict)
    ]


migrar_memoria_legada()
memoria = carregar_memoria()


def carregar_comandos_aprendidos():
    if not ARQUIVO_COMANDOS_APRENDIDOS.exists():
        return {}

    try:
        dados = json.loads(
            ARQUIVO_COMANDOS_APRENDIDOS.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError):
        return {}

    if not isinstance(dados, dict):
        return {}

    return {
        str(frase): str(acao)
        for frase, acao in dados.items()
        if str(frase).strip() and str(acao).strip()
    }


comandos_aprendidos = carregar_comandos_aprendidos()


def salvar_comandos_aprendidos():
    if not escrever_json_atomico(ARQUIVO_COMANDOS_APRENDIDOS, comandos_aprendidos):
        log.error("Não consegui salvar os comandos aprendidos")


def aprender_comando(comando_normalizado):
    separadores = [" faca ", " faz ", " significa "]
    inicio = "quando eu disser "

    if comando_normalizado.startswith(inicio):
        restante = comando_normalizado[len(inicio):]
    elif comando_normalizado.startswith("aprenda que "):
        restante = comando_normalizado[len("aprenda que "):]
    else:
        return False

    for separador in separadores:
        if separador not in restante:
            continue

        frase, acao = restante.split(separador, 1)
        frase = frase.strip()
        acao = acao.strip()
        if not frase or not acao:
            break

        comandos_aprendidos[frase] = acao
        salvar_comandos_aprendidos()
        falar(f"Aprendi: quando você disser {frase}, vou executar {acao}.")
        return True

    falar("Diga: quando eu disser frase, faça comando.")
    return True


def registrar_memoria(tipo, texto):
    """Anexa uma linha no histórico.

    Antes, cada mensagem reescrevia o arquivo inteiro (que só cresce): com o
    uso, cada frase ficava mais lenta e a interface podia ler o arquivo no
    instante em que ele estava truncado. Agora é só um anexo — e o arquivo é
    trocado de lugar quando passa de 8 MB.
    """

    registro = {
        "data": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "tipo": tipo,
        "texto": texto,
    }

    memoria.append(registro)

    # A conversa em memória não precisa crescer sem limite.
    if len(memoria) > MEMORIAS_EM_RAM:
        del memoria[:-MEMORIAS_EM_RAM]

    if not anexar_jsonl(ARQUIVO_MEMORIA, registro):
        log.error("Não consegui gravar a memória em %s", ARQUIVO_MEMORIA)


def ler_comandos_interface():
    """Comandos vindos do painel.

    Cada comando é um arquivo próprio numa fila (jarvis_comum), então nada
    some se o painel escrever no exato momento em que o Jarvis lê — o que
    acontecia com o antigo `comandos_interface.json`.
    """

    return [
        str(item.get("texto", "")).strip()
        for item in consumir_comandos()
        if isinstance(item, dict) and str(item.get("texto", "")).strip()
    ]


def informar_interface(tipo, dados=None):
    """Manda um recado para o painel (aviso, estado da voz, navegação...)."""

    if not publicar_evento(tipo, dados):
        log.warning("Não consegui avisar a interface: %s", tipo)


def navegar_no_painel(url):
    informar_interface("navegar_navegador", {"url": url})


def obter_voz_disponivel():
    comando = (
        "Add-Type -AssemblyName System.Speech; "
        "$voz = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$voz.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }"
    )

    resultado = subprocess.run(
        ["powershell", "-NoProfile", "-Command", comando],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    if resultado.returncode != 0:
        return []

    nomes = []
    for linha in resultado.stdout.splitlines():
        nome = linha.strip()
        if nome:
            nomes.append(nome)

    return nomes


def selecionar_voz_windows():
    global voz_windows, voz_windows_carregada

    if voz_windows_carregada:
        return voz_windows

    favoritas = [
        "Microsoft Maria Desktop",
        "Microsoft Zira Desktop",
        "Microsoft David Desktop",
        "Microsoft Mark Desktop",
        "Microsoft Aria Desktop",
        "Microsoft Guy Hargraves",
        "Microsoft Hazel Desktop"
    ]

    vozes = obter_voz_disponivel()

    for nome in favoritas:
        if nome in vozes:
            voz_windows = nome
            voz_windows_carregada = True
            return voz_windows

    if vozes:
        voz_windows = vozes[0]

    voz_windows_carregada = True
    return voz_windows


def falar(texto):
    """Fala uma frase.

    O caminho normal é o motor persistente (jarvis_tts): um único processo de
    fala para toda a sessão. Se ele não estiver disponível (fora do Windows,
    PowerShell bloqueado, voz ausente), cai no processo único por frase — o
    comportamento antigo — para nunca ficar muda.
    """

    global motor_de_fala, processo_fala, tempo_ultima_fala

    print(f"Jarvis: {texto}")
    registrar_memoria("Jarvis", texto)
    tempo_ultima_fala = time.monotonic()

    if os.name != "nt":
        # Fora do Windows não existe System.Speech: o texto sai no console.
        log.info("(voz indisponível neste sistema) %s", texto)
        return

    if motor_de_fala is None:
        motor_de_fala = MotorDeFala(logger=log, voz=selecionar_voz_windows())
        atexit.register(motor_de_fala.fechar)

    if motor_de_fala.falar(texto):
        return

    log.warning(
        "Motor de fala persistente indisponível (%s); usando o modo antigo",
        motor_de_fala.ultimo_erro,
    )
    processo_fala = _falar_em_processo_unico(texto)


def _falar_em_processo_unico(texto):
    """Reserva: um PowerShell por frase (antes era o único caminho)."""

    voz = selecionar_voz_windows()

    comando_powershell = (
        "Add-Type -AssemblyName System.Speech; "
        "$voz = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$voz.Rate = 1; $voz.Volume = 100; "
        + (f"$voz.SelectVoice('{voz}'); " if voz else "")
        + f"$voz.Speak({json.dumps(texto, ensure_ascii=False)})"
    )
    comando_codificado = base64.b64encode(
        comando_powershell.encode("utf-16le")
    ).decode("ascii")

    try:
        return subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-EncodedCommand", comando_codificado],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=CRIAR_SEM_JANELA,
        )
    except (FileNotFoundError, OSError) as erro:
        log.error("Nem o modo antigo de fala funcionou: %s", erro)
        return None


def esta_falando():
    """A Jarvis está falando agora? (motor persistente ou processo único)"""

    if motor_de_fala is not None and motor_de_fala.falando():
        return True

    return processo_fala is not None and processo_fala.poll() is None


# `normalizar` agora vive em jarvis_intencoes (importada acima), junto com o
# resto do entendimento da fala — uma implementação só, testada uma vez.


def extrair_comando_apos_ativacao(frase):
    palavras = normalizar(frase).split()

    variacoes = [
        "jarvis",
        "jarves",
        "jarviz",
        "jarvi",
        "javis",
        "jarvises",
        "jarvisse",
        "chaves",
        "james",
        "chavis",
        "javi"
    ]

    for indice, palavra in enumerate(palavras):
        if palavra in variacoes:
            return " ".join(palavras[indice + 1:])

        parecido = difflib.get_close_matches(
            palavra,
            ["jarvis"],
            n=1,
            cutoff=0.68
        )

        if parecido:
            return " ".join(palavras[indice + 1:])

    return None


def palavras_importantes(texto):
    ignoradas = {
        "a", "o", "as", "os", "um", "uma",
        "de", "da", "do", "das", "dos",
        "em", "no", "na", "e", "ou",
        "por", "para", "com", "sem",
        "que", "qual", "quais", "como",
        "meu", "minha", "me", "eu",
        "voce", "jarvis", "porfavor"
    }

    return {
        palavra for palavra in normalizar(texto).split()
        if palavra not in ignoradas and len(palavra) > 2
    }


def buscar_memorias_relevantes(pergunta):
    palavras_pergunta = palavras_importantes(pergunta)
    resultados = []

    if not palavras_pergunta:
        return ""

    for indice, item in enumerate(memoria):
        tipo = normalizar(item.get("tipo", "")).replace(" ", "")

        if not tipo.startswith("usu"):
            continue

        texto = item.get("texto", "")

        pontos = len(
            palavras_pergunta.intersection(
                palavras_importantes(texto)
            )
        )

        if pontos > 0:
            resultados.append((pontos, indice, item))

    resultados.sort(
        key=lambda resultado: (resultado[0], resultado[1]),
        reverse=True
    )

    return "\n".join(
        f"{item.get('data', '')} - Usuário: {item.get('texto', '')}"
        for _, _, item in resultados[:8]
    )


def obter_contexto_recente():
    return "\n".join(
        f"{item.get('tipo', 'Jarvis')}: {item.get('texto', '')[:300]}"
        for item in memoria[-6:]
    )


def capturar_tela():
    with mss.mss() as capturador:
        monitor = capturador.monitors[1]
        imagem_mss = capturador.grab(monitor)

        imagem = Image.frombytes(
            "RGB",
            imagem_mss.size,
            imagem_mss.rgb
        )

    return imagem


def descrever_tela_com_ollama():
    if not tela_permitida():
        falar(
            "A permissão de tela está desativada. "
            "Ative-a nas configurações do painel da Jarvis."
        )
        return

    try:
        imagem = capturar_tela()
        texto_tela = pytesseract.image_to_string(
            imagem,
            lang="por+eng",
            config="--psm 11"
        )

        if not texto_tela.strip():
            falar("Não consegui ler textos na tela.")
            return

        instrucao = (
            "Descreva brevemente o que aparece nesta tela com base no texto extraído. "
            "Responda em português brasileiro, de forma curta e objetiva.\n\n"
            f"Texto extraído:\n{texto_tela[:8000]}"
        )
        texto = perguntar_ao_ollama(instrucao)
        falar(texto or "Não consegui identificar conteúdo relevante na tela.")
    except Exception:
        log.exception("Falha ao descrever a tela")
        falar(
            "Não consegui analisar a tela com a IA local. Verifique se o Ollama está instalado."
        )


def encontrar_texto_na_tela(texto_procurado):
    imagem = capturar_tela()

    dados = pytesseract.image_to_data(
        imagem,
        lang="por+eng",
        config="--psm 11",
        output_type=pytesseract.Output.DICT
    )

    alvo = normalizar(texto_procurado).split()
    palavras = []

    for indice, texto in enumerate(dados["text"]):
        texto_limpo = normalizar(texto)

        if texto_limpo:
            palavras.append({
                "texto": texto_limpo,
                "x": dados["left"][indice],
                "y": dados["top"][indice],
                "largura": dados["width"][indice],
                "altura": dados["height"][indice]
            })

    if not alvo:
        return None

    for inicio in range(len(palavras)):
        trecho = palavras[inicio:inicio + len(alvo)]

        if len(trecho) != len(alvo):
            continue

        palavras_encontradas = [
            item["texto"] for item in trecho
        ]

        if palavras_encontradas == alvo:
            esquerda = min(item["x"] for item in trecho)
            topo = min(item["y"] for item in trecho)

            direita = max(
                item["x"] + item["largura"]
                for item in trecho
            )

            baixo = max(
                item["y"] + item["altura"]
                for item in trecho
            )

            return {
                "x": int((esquerda + direita) / 2),
                "y": int((topo + baixo) / 2)
            }

    return None


def preparar_clique(texto_procurado):
    global clique_pendente

    if not tela_permitida():
        falar(
            "A permissão de tela está desativada. "
            "Ative-a nas configurações do painel da Jarvis."
        )
        return

    falar(f"Procurando {texto_procurado} na tela.")

    try:
        posicao = encontrar_texto_na_tela(texto_procurado)

        if posicao is None:
            falar(
                f"Não encontrei o texto {texto_procurado} na tela. "
                "Tente dizer um nome mais curto ou mais exato."
            )
            return

        x, y = para_coordenadas_de_clique(posicao["x"], posicao["y"])

        if (x, y) != (posicao["x"], posicao["y"]):
            log.warning(
                "Clique convertido da escala de vídeo %.2f: (%s, %s) -> (%s, %s)",
                escala_de_video(), posicao["x"], posicao["y"], x, y,
            )

        clique_pendente = {
            "texto": texto_procurado,
            "x": x,
            "y": y,
        }

        falar(
            f"Encontrei {texto_procurado}. "
            "Diga confirmar clique para eu clicar."
        )

    except Exception:
        log.exception("Falha ao procurar texto na tela")
        falar("Não consegui procurar esse texto na tela.")


def confirmar_clique():
    global clique_pendente

    if clique_pendente is None:
        falar("Não existe nenhum clique aguardando confirmação.")
        return

    try:
        pyautogui.click(
            clique_pendente["x"],
            clique_pendente["y"]
        )

        falar(f"Cliquei em {clique_pendente['texto']}.")
        clique_pendente = None

    except Exception:
        log.exception("Falha ao clicar")
        falar("Não consegui realizar o clique.")


def cancelar_clique():
    global clique_pendente

    clique_pendente = None
    falar("Clique cancelado.")


def iniciar_ollama_se_necessario():
    """Tenta subir o `ollama serve` se ele estiver instalado."""

    caminho = CAMINHO_OLLAMA or localizar_ollama()

    if caminho is None:
        raise RuntimeError("Ollama não encontrado no sistema.")

    log.info("Iniciando o Ollama em %s", caminho)
    subprocess.Popen(
        [str(caminho), "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CRIAR_SEM_JANELA,
    )


def perguntar_ao_ollama(instrucao):
    """Consulta o modelo local, com uma segunda tentativa de verdade.

    Antes, se o executável não estivesse no caminho fixo, a exceção de
    `iniciar_ollama_se_necessario` escapava do `except` e a segunda tentativa
    nunca acontecia.
    """

    dados = json.dumps({
        "model": modelo_local(),
        "prompt": instrucao,
        "stream": False,
        "options": {
            "temperature": 0.4,
            "num_predict": 256
        }
    }).encode("utf-8")

    ultimo_erro = ""

    for tentativa in range(2):
        requisicao = Request(
            OLLAMA_URL,
            data=dados,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        try:
            with urlopen(requisicao, timeout=TEMPO_DE_RESPOSTA_DO_OLLAMA) as resposta:
                resultado = json.loads(resposta.read().decode("utf-8"))
            return resultado.get("response", "").strip()
        except Exception as erro:
            ultimo_erro = f"{type(erro).__name__}: {erro}"
            log.warning("Ollama não respondeu (tentativa %s): %s", tentativa + 1, ultimo_erro)

            if tentativa == 0:
                try:
                    iniciar_ollama_se_necessario()
                except Exception:
                    log.exception("Não consegui iniciar o Ollama")
                time.sleep(2)

    raise RuntimeError(f"O Ollama não respondeu ({ultimo_erro}).")


def responder_com_ollama(pergunta):
    print("Jarvis: Pensando...")
    informar_interface("estado_voz", {"processando": True, "motivo": "pensando"})

    if filtro_de_palavroes_ativado():
        regra_de_palavroes = (
            "Não use palavrões. Troque linguagem ofensiva por linguagem educada. "
        )
    else:
        regra_de_palavroes = (
            "Pode usar palavrões comuns quando combinarem com a conversa e "
            "não use asteriscos para censurar palavras. "
        )

    try:
        instrucao = (
            "Você é Jarvis, uma assistente de voz pessoal. "
            "Responda em português brasileiro, de forma curta, direta e informal. "
            f"{regra_de_palavroes}"
            "Nunca diga que abriu, pesquisou, corrigiu, clicou ou executou uma ação "
            "no computador, porque você só está respondendo uma pergunta. "
            "Se o usuário pedir uma ação, diga que ele precisa usar um comando "
            "específico da Jarvis.\n\n"
            f"Memórias relevantes:\n{buscar_memorias_relevantes(pergunta)}\n\n"
            f"Histórico recente:\n{obter_contexto_recente()}\n\n"
            f"Pergunta atual: {pergunta}"
        )

        texto = perguntar_ao_ollama(instrucao)

        if texto:
            falar(texto)
        else:
            falar("Não consegui criar uma resposta agora.")

    except Exception:
        log.exception("Falha ao consultar a IA local")
        falar(
            "Não consegui acessar a IA local. Verifique se o Ollama está instalado."
        )
    finally:
        informar_interface("estado_voz", {"processando": False})


def listar_aplicativos(forcar=False):
    """Lista os aplicativos do menu Iniciar (Windows), com cache.

    Dois problemas resolvidos aqui:

    * antes, se o PowerShell não estivesse no PATH, o ``subprocess.run``
      levantava FileNotFoundError e o comando "abrir <app>" morria calado —
      justamente o comando mais usado;
    * ``Get-StartApps`` é lento (centenas de apps) e rodava **a cada** "abrir
      X", travando o laço principal por segundos. Agora a lista fica em cache
      por 10 minutos; uma lista vazia (falha) é tentada de novo em 30 s.
    """

    global _aplicativos_cache, _aplicativos_cache_ate

    agora = time.monotonic()

    if not forcar and _aplicativos_cache and agora < _aplicativos_cache_ate:
        return dict(_aplicativos_cache)

    aplicativos = _listar_aplicativos_do_windows()

    if aplicativos:
        _aplicativos_cache = aplicativos
        _aplicativos_cache_ate = agora + CACHE_DE_APLICATIVOS
    else:
        # Falhou: não guarda o vazio por 10 minutos (senão o usuário fica sem
        # conseguir abrir nada até o cache expirar).
        _aplicativos_cache = {}
        _aplicativos_cache_ate = agora + 30.0

    return dict(aplicativos)


def _listar_aplicativos_do_windows():
    if os.name != "nt":
        log.info("Listagem de aplicativos do menu Iniciar só existe no Windows")
        return {}

    comando = (
        "Get-StartApps | "
        "Select-Object Name, AppID | "
        "ConvertTo-Json -Compress"
    )

    try:
        resultado = subprocess.run(
            ["powershell", "-NoProfile", "-Command", comando],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=25,
            creationflags=CRIAR_SEM_JANELA,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as erro:
        log.error("Não consegui listar os aplicativos: %s", erro)
        return {}

    if not resultado.stdout.strip():
        log.warning("Listagem de aplicativos voltou vazia (código %s)", resultado.returncode)
        return {}

    try:
        dados = json.loads(resultado.stdout)
    except json.JSONDecodeError:
        log.warning("Listagem de aplicativos em formato inesperado")
        return {}

    if isinstance(dados, dict):
        dados = [dados]

    aplicativos = {}

    for item in dados:
        nome = item.get("Name", "")
        app_id = item.get("AppID", "")

        if nome and app_id:
            aplicativos[normalizar(nome)] = (nome, app_id)

    return aplicativos


def abrir_aplicativo(nome_falado):
    aplicativos = listar_aplicativos()
    nome = normalizar(nome_falado)

    if not nome:
        falar("Diga o nome do aplicativo que você quer abrir.")
        return

    for artigo in ["o ", "a ", "os ", "as "]:
        if nome.startswith(artigo):
            nome = nome[len(artigo):]

    for nome_app, dados in aplicativos.items():
        palavras_nome = nome_app.split()
        if nome == nome_app or nome in palavras_nome:
            nome_original, app_id = dados

            if abrir_pelo_id(app_id):
                falar(f"Abrindo {nome_original}.")
            return

    parecidos = difflib.get_close_matches(
        nome,
        aplicativos.keys(),
        n=1,
        cutoff=0.78
    )

    if parecidos:
        # Nome parecido não é nome certo: abrir o programa errado é pior do que
        # perguntar. Antes o Jarvis abria direto o "mais parecido".
        nome_original, app_id = aplicativos[parecidos[0]]
        pedir_confirmacao(
            Intencao("abrir_aplicativo", 0.80, nome_original, "parecido", nome_falado),
            pergunta=(
                f"Não encontrei {nome_falado}. "
                f"Você quis dizer {nome_original}? Diga sim para eu abrir."
            ),
        )
    else:
        log.info("Aplicativo não encontrado: %r", nome_falado)
        falar(f"Não encontrei o aplicativo {nome_falado}.")


def abrir_pelo_id(app_id):
    """Pede ao Explorer para abrir o aplicativo do menu Iniciar."""

    try:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])
        return True
    except (FileNotFoundError, OSError) as erro:
        log.error("Não consegui abrir %s: %s", app_id, erro)
        falar("Não consegui abrir esse aplicativo.")
        return False


def termo_de_pesquisa(pedido):
    """O que pesquisar, sem o verbo nem o "na internet" (palavras inteiras)."""

    return _sem_verbo_inicial(pedido, "pesquisar")


def pesquisar_na_internet(pedido):
    pesquisa = termo_de_pesquisa(pedido)

    if not pesquisa:
        falar("Diga o que você quer pesquisar.")
        return

    link = "https://www.google.com/search?q=" + quote_plus(pesquisa)
    navegar_no_painel(link)
    falar(f"Pesquisando por {pesquisa}.")


def _sem_verbo_inicial(texto, nome_intencao):
    """Tira o verbo e o vocabulário do comando do começo do pedido.

    Devolve ``""`` quando não sobra pedido nenhum — é o que faz o Jarvis
    perguntar "qual música?" em vez de abrir uma busca por "youtube".

    A regra do verbo é **conservadora** de propósito: ele só sai quando o resto
    não diz nada por si ("tocar", "tocar no youtube", "pesquisar na internet").
    Se o resto tem conteúdo, a primeira palavra pode muito bem ser o começo do
    título — "toque de midas" e "toca do coelho" são os dois casos reais que
    motivaram isto. Melhor sobrar um verbo na busca do que perder o título.
    """

    fora = PALAVRAS_FORA_DO_ARGUMENTO.get(nome_intencao, frozenset())
    conectivos = ENCHIMENTO_DE_ARGUMENTO | LIXO_INICIAL_DO_ARGUMENTO
    palavras = normalizar(texto).split()

    def tem_conteudo(lista):
        return any(
            palavra not in fora
            and palavra not in ENCHIMENTO_DE_ARGUMENTO
            and palavra not in EQUIVALENTES_DE_VERBO
            for palavra in lista
        )

    # "gerar imagem pôr do sol" digitado no painel repete o comando na frente.
    # Só sai o que é claramente prefixo: o vocabulário do comando, ou o verbo
    # seguido desse vocabulário. "toca" de "toca do coelho" não é prefixo.
    while palavras:
        if palavras[0] in fora:
            palavras = palavras[1:]
            continue

        if len(palavras) > 1 and palavras[0] in EQUIVALENTES_DE_VERBO:
            resto = palavras[1:]
            if resto[0] in fora or (
                resto[0] in conectivos and len(resto) > 1 and resto[1] in fora
            ):
                palavras = resto
                continue

        break

    if not palavras:
        return ""

    # Verbo dito duas vezes ("toca toca do coelho"): o primeiro comanda. Só vale
    # quando o segundo não começa um título ("toca toque de midas" tem "de" logo
    # depois, então os dois fazem parte do nome).
    if (
        len(palavras) > 2
        and palavras[0] in EQUIVALENTES_DE_VERBO
        and palavras[1] in EQUIVALENTES_DE_VERBO
        and palavras[2] not in conectivos
        and tem_conteudo(palavras[2:])
    ):
        palavras = palavras[1:]

    if palavras[0] in EQUIVALENTES_DE_VERBO:
        if not tem_conteudo(palavras[1:]):
            return ""

        palavras = palavras[1:]

    return extrair_argumento(palavras, nome_intencao)


def nome_da_musica(pedido):
    """Extrai o nome da música do pedido, por palavras inteiras.

    Antes era uma sequência de ``str.replace`` sem fronteira de palavra: em
    "Toque de Midas" o "toque" do verbo era removido **de dentro do título** e a
    busca saía por "de midas".
    """

    return _sem_verbo_inicial(pedido, "tocar_musica")


def tocar_no_youtube(pedido):
    musica = nome_da_musica(pedido)

    if not musica:
        falar("Diga qual música você quer tocar.")
        return

    link = "https://www.youtube.com/results?search_query=" + quote_plus(musica)
    navegar_no_painel(link)
    falar(f"Abrindo {musica} no YouTube.")


def tocar_no_youtube_music(pedido):
    musica = nome_da_musica(pedido)

    if not musica:
        falar("Diga qual música você quer tocar.")
        return

    link = "https://music.youtube.com/search?q=" + quote_plus(musica)

    navegar_no_painel(link)
    falar(f"Abrindo {musica} no YouTube Music.")


def enviar_tecla_de_midia(codigo_tecla, repeticoes=1):
    """Envia uma tecla de mídia do Windows (volume, faixa, play/pausa).

    Duas correções em relação ao original:

    * não explode fora do Windows (o acesso era direto a `ctypes.windll`);
    * repete o toque, porque um único `VK_VOLUME_UP` muda só ~2% do volume.
    """

    if os.name != "nt":
        log.warning("Teclas de mídia não existem neste sistema (0x%X)", codigo_tecla)
        return False

    try:
        usuario32 = ctypes.WinDLL("user32", use_last_error=True)
    except Exception:
        log.exception("Não consegui acessar a API de teclado do Windows")
        return False

    try:
        for _ in range(max(1, int(repeticoes))):
            usuario32.keybd_event(codigo_tecla, 0, 0, 0)
            usuario32.keybd_event(codigo_tecla, 0, 2, 0)
            time.sleep(0.03)
        return True
    except Exception:
        log.exception("Falha ao enviar a tecla de mídia 0x%X", codigo_tecla)
        return False


def controlar_musica(comando_normalizado, passos_de_volume=5):
    """Teclas de mídia. O volume dá vários toques (um só quase não muda nada)."""

    acoes = [
        (("pausar", "pause", "parar musica", "parar a musica"), 0xB3, 1, "Pausando a música."),
        (("continuar musica", "continuar a musica", "reproduzir musica", "dar play", "play"),
         0xB3, 1, "Reproduzindo a música."),
        (("proxima musica", "proxima faixa", "pular musica", "pular faixa"),
         0xB0, 1, "Próxima música."),
        (("musica anterior", "faixa anterior", "voltar musica"), 0xB1, 1, "Música anterior."),
        (("aumentar volume", "aumente o volume"), 0xAF, passos_de_volume, "Aumentando o volume."),
        (("diminuir volume", "diminua o volume"), 0xAE, passos_de_volume, "Diminuindo o volume."),
        (("silenciar", "mutar"), 0xAD, 1, "Áudio alternado."),
    ]

    for gatilhos, tecla, repeticoes, resposta in acoes:
        if not any(texto in comando_normalizado for texto in gatilhos):
            continue

        if enviar_tecla_de_midia(tecla, repeticoes):
            falar(resposta)
        else:
            falar("Não consegui controlar o áudio neste computador.")

        return True

    return False


def corrigir_texto_copiado(colar_depois=False):
    try:
        texto_original = (pyperclip.paste() or "").strip()
    except Exception:
        log.exception("Não consegui ler a área de transferência")
        falar("Não consegui ler o texto copiado.")
        return

    if not texto_original:
        falar(
            "Não encontrei texto copiado. Selecione o texto e aperte Control C antes de pedir."
        )
        return

    if len(texto_original) > 12000:
        falar("O texto copiado é muito grande. Copie uma parte menor para eu corrigir.")
        return

    print("Jarvis: Corrigindo texto...")

    try:
        instrucao = (
            "Corrija somente ortografia, pontuação, concordância e clareza "
            "do texto abaixo em português brasileiro. "
            "Não explique nada, não use aspas e não adicione título. "
            "Devolva apenas o texto corrigido.\n\n"
            f"Texto:\n{texto_original}"
        )

        texto_corrigido = perguntar_ao_ollama(instrucao)

        if not texto_corrigido:
            falar("Não consegui corrigir esse texto.")
            return

        try:
            pyperclip.copy(texto_corrigido)
        except Exception:
            log.exception("Não consegui escrever na área de transferência")
            falar("Não consegui copiar o texto corrigido.")
            return

        if colar_depois:
            falar("Texto corrigido. Vou colar em dois segundos.")
            time.sleep(2)
            pyautogui.hotkey("ctrl", "v")
            falar("Texto corrigido e colado.")
        else:
            falar(
                "Corrigi o texto e deixei a versão corrigida copiada. "
                "Aperte Control V onde quiser colar."
            )

    except Exception:
        log.exception("Falha ao corrigir texto copiado")
        falar("Não consegui corrigir o texto agora.")


def processar_comando(comando, profundidade=0):
    """Interpreta e executa um comando.

    ``profundidade`` protege contra comandos aprendidos em ciclo (A -> B -> A),
    que antes causavam RecursionError.
    """

    comando = (comando or "").strip()

    if not comando:
        return True

    if profundidade > 4:
        log.error("Comandos aprendidos em ciclo; interrompendo (%r)", comando)
        falar("Os comandos aprendidos estão em ciclo. Remova um deles.")
        return True

    comando_normalizado = normalizar(comando)

    registrar_memoria("Usuário", comando)
    log.info("Comando recebido: %r", comando)

    if comando_normalizado.startswith("quando eu disser ") or comando_normalizado.startswith("aprenda que "):
        aprender_comando(comando_normalizado)
        return True

    acao_aprendida = comandos_aprendidos.get(comando_normalizado)
    if acao_aprendida and acao_aprendida != comando_normalizado:
        falar(f"Executando o comando aprendido: {comando_normalizado}.")
        return processar_comando(acao_aprendida, profundidade + 1)

    # Uma confirmação já estava esperando resposta?
    resposta_pendente = tratar_intencao_pendente(comando_normalizado)

    if resposta_pendente is not None:
        return resposta_pendente

    if clique_pendente is not None and comando_normalizado in [
        "sim",
        "confirmar",
        "confirmar clique",
        "pode clicar"
    ]:
        confirmar_clique()
        return True

    if clique_pendente is not None and comando_normalizado in [
        "nao",
        "cancelar",
        "cancelar clique"
    ]:
        cancelar_clique()
        return True

    intencao = reconhecer(comando)

    if intencao is not None and intencao.nome in ("confirmar", "cancelar"):
        intencao = None

    # Caminho do agente: pedido encadeado ("abre o chrome e pesquisa X") ou
    # pedido que o reconhecedor não entendeu. Se o agente não estiver ligado,
    # ou não conseguir responder, tudo segue pelo caminho antigo logo abaixo.
    if (
        agente_ativado()
        and (intencao is None or eh_comando_encadeado(comando_normalizado))
        and responder_com_agente(comando, intencao_de_reserva=intencao)
    ):
        return True

    if intencao is None:
        # Conversa livre (ou um "sim" sem nada pendente): vai para a IA local.
        informar_interface("estado_voz", {"processando": True, "motivo": "consultando a IA local"})
        responder_com_ollama(comando)
        return True

    if intencao.precisa_confirmar:
        faltando = argumento_que_falta(intencao)

        if faltando:
            # "Gerar imagem." / "Você quis dizer: abrir um programa?" não dizem
            # nada: o pedido veio sem o essencial, então pergunta-se o essencial.
            falar(faltando)
            return True

        return pedir_confirmacao(intencao)

    return executar_intencao(intencao)


def argumento_que_falta(intencao):
    """Frase para pedir o que o usuário esqueceu de dizer — ou ``None``."""

    if intencao.argumento:
        return None

    return {
        "gerar_imagem": "Diga o que você quer na imagem.",
        "abrir_aplicativo": "Diga o nome do programa que devo abrir.",
        "tocar_musica": "Diga qual música você quer tocar.",
        "pesquisar": "Diga o que você quer pesquisar.",
        "clicar_em": "Diga em que devo clicar.",
        "corrigir_texto": "Copie o texto que devo corrigir e peça de novo.",
    }.get(intencao.nome)


def pedir_confirmacao(intencao, pergunta=None):
    """Frase parecida com um comando: pergunta antes de agir.

    ``pergunta`` permite uma frase própria (usada quando o programa não existe e
    o Jarvis achou um nome parecido — aí "Você quis dizer: abrir o programa X"
    não explica o que aconteceu).
    """

    global intencao_pendente

    intencao_pendente = intencao
    descricao = descrever_intencao(intencao)

    log.info(
        "Pedindo confirmação: %s (confiança %.2f, ouvido %r)",
        descricao,
        intencao.confianca,
        intencao.frase,
    )

    falar(pergunta or f"Você quis dizer: {descricao}? Diga sim para eu fazer.")
    informar_interface(
        "aviso",
        {"nivel": "info", "texto": f"Esperando confirmação para {descricao}"},
    )
    return True


def tratar_intencao_pendente(comando_normalizado):
    """Responde ao sim/não de uma intenção aguardando confirmação.

    Devolve ``None`` quando não há nada pendente — aí o comando novo segue o
    fluxo normal, sem ficar preso esperando uma resposta que não vem.
    """

    global intencao_pendente, ferramenta_pendente

    if intencao_pendente is None:
        return None

    intencao = intencao_pendente

    if comando_normalizado in [
        "sim", "confirmar", "confirmo", "isso", "pode", "pode sim",
        "claro", "beleza", "ok", "esta certo", "tudo bem",
    ]:
        intencao_pendente = None
        log.info("Usuário confirmou: %s", intencao.nome)
        return executar_intencao(intencao)

    if comando_normalizado in [
        "nao", "cancelar", "deixa", "deixa pra la", "nao quero", "errado",
    ]:
        intencao_pendente = None
        ferramenta_pendente = None
        falar("Tudo bem, não vou fazer.")
        return True

    # Veio outra coisa no lugar do sim/não: a confirmação morre com a dúvida.
    intencao_pendente = None
    ferramenta_pendente = None
    return None


def gerar_imagem(descricao):
    """Trata o botão "Gerar imagem" do painel.

    O Jarvis não tem gerador de imagens. Antes, o pedido caía no modelo de
    linguagem e sumia sem resposta. Agora ele é honesto e útil: abre uma
    busca de imagens com a descrição.

    ``descricao`` já chega limpa pela camada de intenções (sem o "gerar imagem"
    do pedido). O corte por prefixo continua aqui só para o texto que vem
    digitado do painel, que não passa por ela.
    """

    descricao = _sem_verbo_inicial(descricao, "gerar_imagem")

    # "imagem" sozinho não descreve nada: quem tratou a intenção mandou o
    # pedido inteiro porque o usuário não disse o quê.
    if descricao.strip() in {"", "imagem", "uma imagem", "imagens"}:
        falar("Diga o que você quer na imagem.")
        return

    log.info("Pedido de imagem (recurso não disponível): %r", descricao)
    navegar_no_painel(
        "https://www.google.com/search?tbm=isch&q=" + quote_plus(descricao)
    )
    falar(
        f"Ainda não sei gerar imagens. Abri uma busca de imagens sobre {descricao}."
    )


def executar_intencao(intencao):
    """Executa o comando entendido. Devolve False só quando é para encerrar."""

    nome = intencao.nome
    frase = intencao.frase or ""
    argumento = intencao.argumento or ""

    log.info("Executando intenção %s (confiança %.2f)", nome, intencao.confianca)

    try:
        if nome == "ferramenta_do_agente":
            return executar_ferramenta_pendente()

        if nome == "sair":
            falar("Até logo!")
            return False

        if nome == "hora":
            falar(f"Agora são {datetime.now().strftime('%H:%M')}.")
            return True

        if nome == "aprender":
            aprender_comando(frase)
            return True

        if nome == "descrever_tela":
            descrever_tela_com_ollama()
            return True

        if nome == "corrigir_e_colar":
            corrigir_texto_copiado(colar_depois=True)
            return True

        if nome == "corrigir_texto":
            corrigir_texto_copiado(colar_depois=False)
            return True

        if nome == "esconder_painel":
            informar_interface("esconder_painel", {})
            falar("Escondendo o painel. Para abrir de novo, use o ícone perto do relógio.")
            return True

        if nome == "holograma":
            ligar = quer_ligar(intencao)
            informar_interface("mostrar_holograma", {"visivel": ligar})
            falar("Holograma ligado." if ligar else "Holograma desligado.")
            return True

        if nome == "camera":
            ligar = quer_ligar(intencao)
            informar_interface("definir_camera", {"ativada": ligar})
            falar("Câmera ligada." if ligar else "Câmera desligada.")
            return True

        if nome == "musica_controle":
            controlar_musica(frase)
            return True

        if nome == "abrir_youtube_music":
            navegar_no_painel("https://music.youtube.com")
            falar("Abrindo o YouTube Music.")
            return True

        if nome == "abrir_navegador":
            navegar_no_painel("https://www.google.com")
            falar("Abrindo o navegador.")
            return True

        if nome == "tocar_musica":
            pedido = argumento or frase

            if "youtube music" in frase or "youtube musica" in frase:
                tocar_no_youtube_music(pedido)
            else:
                tocar_no_youtube(pedido)
            return True

        if nome == "pesquisar":
            pesquisar_na_internet(argumento or frase)
            return True

        if nome == "clicar_em":
            if not argumento:
                falar("Diga em que devo clicar.")
                return True

            preparar_clique(argumento)
            return True

        if nome == "gerar_imagem":
            gerar_imagem(argumento or frase)
            return True

        if nome == "abrir_aplicativo":
            if not argumento:
                falar("Diga o nome do programa que devo abrir.")
                return True

            abrir_aplicativo(argumento)
            return True

    except Exception:
        log.exception("Falha ao executar a intenção %s (frase %r)", nome, frase)
        falar("Não consegui fazer isso agora.")
        return True

    log.warning("Intenção sem tratamento: %r", nome)
    responder_com_ollama(frase)
    return True


# ---------------------------------------------------------------------------
# Ferramentas do agente (o cérebro que decide)
# ---------------------------------------------------------------------------
#
# O reconhecedor de intenções continua sendo o caminho rápido: comando que ele
# entende com confiança alta (ou que já está sendo confirmado) não passa por
# aqui — "aumenta o volume" tem que ser instantâneo. As ferramentas entram para
# o que a lista de frases não cobre: pedido novo, pedido com duas ações, ou
# pedido que não é comando nenhum.
#
# Cada ferramenta embrulha uma função que já existia, então nada mudou de
# comportamento: quem fala o resultado continua sendo ela (`fala_sozinha=True`),
# e o texto devolvido serve para o modelo saber o que aconteceu e decidir o
# próximo passo.

FERRAMENTAS = RegistroDeFerramentas()
_ferramentas_montadas = False

# Chamada de ferramenta esperando o "sim" do usuário.
ferramenta_pendente = None


def montar_ferramentas():
    """Registra as ferramentas. Roda uma vez, na primeira vez que precisa."""

    global _ferramentas_montadas

    if _ferramentas_montadas and len(FERRAMENTAS):
        return FERRAMENTAS

    _ferramentas_montadas = True
    FERRAMENTAS.limpar()

    def registrar(nome, descricao, funcao, parametros=None, obrigatorios=(), **extras):
        return FERRAMENTAS.registrar(
            Ferramenta(
                nome=nome,
                descricao=descricao,
                executar=funcao,
                parametros=parametros or {},
                obrigatorios=tuple(obrigatorios),
                **extras,
            )
        )

    # -- programas e navegador --------------------------------------------

    registrar(
        "abrir_aplicativo",
        "Abre um programa instalado no computador pelo nome falado "
        "(ex.: bloco de notas, chrome, spotify, calculadora).",
        lambda a: _resultado_de(
            abrir_aplicativo,
            str(a.get("nome", "")),
            texto=f"Pedido de abrir {a.get('nome', '')!r} executado; o usuário já ouviu o resultado.",
        ),
        {"nome": {"type": "string", "description": "Nome do programa, como o usuário fala"}},
        ("nome",),
        intencao="abrir_aplicativo",
    )

    registrar(
        "pesquisar_na_internet",
        "Pesquisa um termo no Google e abre o resultado no navegador do painel.",
        lambda a: _resultado_de(
            pesquisar_na_internet,
            str(a.get("termo", "")),
            texto=f"Pesquisa aberta no painel por {termo_de_pesquisa(str(a.get('termo', '')))!r}.",
        ),
        {"termo": {"type": "string", "description": "O que pesquisar"}},
        ("termo",),
        intencao="pesquisar",
    )

    registrar(
        "abrir_navegador",
        "Abre o Google no navegador do painel.",
        lambda a: _resultado_de(
            navegar_no_painel, "https://www.google.com", texto="Navegador aberto."
        ),
        intencao="abrir_navegador",
    )

    registrar(
        "abrir_youtube_music",
        "Abre o YouTube Music no navegador do painel.",
        lambda a: _resultado_de(
            navegar_no_painel, "https://music.youtube.com", texto="YouTube Music aberto."
        ),
        intencao="abrir_youtube_music",
    )

    # -- música ------------------------------------------------------------

    registrar(
        "tocar_musica",
        "Toca uma música, artista ou playlist. Use servico='youtube_music' "
        "quando o usuário pedir o YouTube Music.",
        lambda a: _tocar_musica_pela_ferramenta(a),
        {
            "nome": {"type": "string", "description": "Música, artista ou playlist"},
            "servico": {
                "type": "string",
                "description": "youtube (padrão) ou youtube_music",
                "enum": ["youtube", "youtube_music"],
            },
        },
        ("nome",),
        intencao="tocar_musica",
    )

    registrar(
        "controlar_musica",
        "Controla o que está tocando: pausar, continuar, próxima, anterior, "
        "aumentar ou diminuir o volume, silenciar.",
        lambda a: _controlar_musica_pela_ferramenta(a),
        {
            "acao": {
                "type": "string",
                "description": "O que fazer com o áudio",
                "enum": [
                    "pausar", "continuar", "proxima", "anterior",
                    "aumentar_volume", "diminuir_volume", "silenciar",
                ],
            }
        },
        ("acao",),
        intencao="musica_controle",
    )

    # -- tela e sistema ----------------------------------------------------

    registrar(
        "que_horas_sao",
        "Diz a hora atual.",
        lambda a: _responder_hora(),
        intencao="hora",
    )

    registrar(
        "descrever_tela",
        "Olha a tela do computador e descreve o que está aberto nela.",
        lambda a: _resultado_de(
            descrever_tela_com_ollama,
            texto="Descrição da tela feita em voz alta ao usuário.",
        ),
        intencao="descrever_tela",
    )

    registrar(
        "clicar_em",
        "Procura um texto na tela e clica nele — o usuário ainda confirma o clique.",
        lambda a: _resultado_de(
            preparar_clique,
            str(a.get("texto", "")),
            texto=f"Clique em {a.get('texto', '')!r} preparado; a Jarvis pediu a confirmação.",
        ),
        {"texto": {"type": "string", "description": "Texto visível na tela, como aparece"}},
        ("texto",),
        intencao="clicar_em",
    )

    registrar(
        "gerar_imagem",
        "O computador ainda não gera imagens de verdade: abre uma busca de "
        "imagens com a descrição pedida.",
        lambda a: _resultado_de(
            gerar_imagem,
            str(a.get("descricao", "")),
            texto="Busca de imagens aberta (a Jarvis avisou que ainda não gera imagens).",
        ),
        {"descricao": {"type": "string", "description": "O que a imagem deveria mostrar"}},
        ("descricao",),
        intencao="gerar_imagem",
    )

    # -- painel e câmera ---------------------------------------------------

    registrar(
        "ligar_holograma",
        "Liga ou desliga o holograma no painel.",
        lambda a: _ligar_holograma(bool(a.get("ligar", True))),
        {"ligar": {"type": "boolean", "description": "true liga, false desliga"}},
        intencao="holograma",
    )

    registrar(
        "ligar_camera",
        "Liga ou desliga a câmera (rosto, expressões, gestos).",
        lambda a: _ligar_camera(bool(a.get("ligar", True))),
        {"ligar": {"type": "boolean", "description": "true liga, false desliga"}},
        intencao="camera",
    )

    registrar(
        "esconder_painel",
        "Esconde a janela da Jarvis (volta pelo ícone perto do relógio).",
        lambda a: _resultado_de(
            informar_interface, "esconder_painel", {}, texto="Painel escondido."
        ),
        confirma=True,
        pedido_de_confirmacao="esconda o painel",
        intencao="esconder_painel",
    )

    # -- ações que mexem no que o usuário está fazendo ---------------------

    registrar(
        "corrigir_texto",
        "Corrige o português do texto que o usuário copiou, opcionalmente colando de volta.",
        lambda a: _corrigir_texto_pela_ferramenta(a),
        {"colar": {"type": "boolean", "description": "true cola o texto corrigido"}},
        confirma=True,
        pedido_de_confirmacao="corrija o texto que você copiou",
        intencao="corrigir_e_colar",
    )

    registrar(
        "sair_da_jarvis",
        "Fecha a Jarvis.",
        lambda a: "Fechando a Jarvis.",
        confirma=True,
        pedido_de_confirmacao="feche a Jarvis",
        intencao="sair",
    )

    # -- ajuda -------------------------------------------------------------

    registrar(
        "listar_capacidades",
        "Devolve a lista do que a Jarvis sabe fazer, para responder "
        "'o que você sabe fazer?'.",
        lambda a: "A Jarvis sabe fazer isto:\n" + FERRAMENTAS.guia_em_texto(),
        fala_sozinha=False,
    )

    log.info("Ferramentas do agente: %s", ", ".join(FERRAMENTAS.nomes()))
    return FERRAMENTAS


def registro_de_ferramentas():
    return montar_ferramentas()


# -- ferramentas que precisam de mais do que uma chamada -------------------


def _tocar_musica_pela_ferramenta(argumentos):
    pedido = str(argumentos.get("nome", ""))
    servico = normalizar(str(argumentos.get("servico", "youtube")))

    if servico == "youtube_music" or "music" in servico:
        tocar_no_youtube_music(pedido)
    else:
        tocar_no_youtube(pedido)

    return f"Tocando {nome_da_musica(pedido)!r} ({servico or 'youtube'})."


def _controlar_musica_pela_ferramenta(argumentos):
    """Traduz a ação do modelo para a frase que o controlador já entende."""

    equivalentes = {
        "pausar": "pausar",
        "continuar": "play",
        "proxima": "proxima musica",
        "anterior": "musica anterior",
        "aumentar_volume": "aumentar volume",
        "diminuir_volume": "diminuir volume",
        "silenciar": "silenciar",
    }

    acao = normalizar(str(argumentos.get("acao", "")))
    frase = equivalentes.get(acao, acao)

    if not controlar_musica(frase):
        return f"Não reconheci a ação de áudio {acao!r}. Use uma das: {', '.join(equivalentes)}."

    return f"Controle de áudio enviado: {acao}."


def _responder_hora():
    agora = datetime.now().strftime("%H:%M")
    falar(f"Agora são {agora}.")
    return f"São {agora}."


def _ligar_holograma(ligar):
    informar_interface("mostrar_holograma", {"visivel": ligar})
    falar("Holograma ligado." if ligar else "Holograma desligado.")
    return f"Holograma {'ligado' if ligar else 'desligado'}."


def _ligar_camera(ligar):
    informar_interface("definir_camera", {"ativada": ligar})
    falar("Câmera ligada." if ligar else "Câmera desligada.")
    return f"Câmera {'ligada' if ligar else 'desligada'}."


def _corrigir_texto_pela_ferramenta(argumentos):
    colar = bool(argumentos.get("colar", False))
    corrigir_texto_copiado(colar_depois=colar)

    return (
        "Texto do usuário corrigido e colado de volta."
        if colar
        else "Texto do usuário corrigido (sem colar)."
    )


# -- decisão: caminho rápido ou agente ------------------------------------


def eh_comando_encadeado(comando_normalizado):
    """A frase pede mais de uma ação?

    "abre o chrome **e** pesquisa o preço do ssd" era entendido como *abrir o
    aplicativo* com o resto dentro do argumento — e o segundo pedido sumia. Com
    duas ou mais formas verbais de comando e uma palavra de ligação, a frase vai
    para o agente, que chama as duas ferramentas.
    """

    if " e " not in f" {comando_normalizado} " and " depois " not in comando_normalizado:
        return False

    verbos = [
        palavra
        for palavra in comando_normalizado.split()
        if palavra in EQUIVALENTES_DE_VERBO
    ]

    return len(verbos) >= 2


def contexto_para_o_agente():
    """O que o modelo precisa saber do momento presente."""

    partes = [f"Agora são {datetime.now().strftime('%d/%m/%Y %H:%M')}."]

    recentes = obter_contexto_recente()

    if recentes:
        partes.append(f"Conversa recente:\n{recentes}")

    aplicativos = listar_aplicativos()

    if aplicativos:
        nomes = list(aplicativos)[:40]
        partes.append("Programas instalados (amostra): " + ", ".join(nomes))

    return "\n".join(partes)


def _resultado_de(funcao, *argumentos, texto):
    """Roda uma função que já fala o resultado e devolve o texto para o modelo."""

    funcao(*argumentos)
    return texto


def executar_ferramenta_do_agente(chamada):
    """Roda uma ferramenta do agente (a exceção nunca escapa)."""

    log.info(
        "Agente pediu a ferramenta %s(%s)",
        chamada.nome,
        json.dumps(chamada.argumentos, ensure_ascii=False, default=str),
    )

    return executar_ferramenta(
        registro_de_ferramentas(), chamada.nome, chamada.argumentos, log=log
    )


def responder_com_agente(pergunta, intencao_de_reserva=None):
    """Deixa o modelo decidir o que fazer, executando ferramentas.

    Devolve ``True`` quando o turno foi tratado aqui. ``False`` significa
    "não deu" — aí quem chama segue pelo caminho antigo, que continua existindo
    inteiro. É esse recuo que garante que a Jarvis funcione mesmo com o Ollama
    desligado, o modelo trocado ou a rede fora.
    """

    global ferramenta_pendente

    config = carregar_config()

    agente = Agente(
        registro=registro_de_ferramentas(),
        modelo=modelo_local(),
        url=OLLAMA_URL.replace("/api/generate", "/api/chat"),
        log=log,
        contexto=contexto_para_o_agente(),
        max_rodadas=int(config.get("rodadas_do_agente", 4) or 4),
    )

    informar_interface("estado_voz", {"processando": True, "motivo": "decidindo o que fazer"})

    try:
        resultado = agente.responder(pergunta)
    except Exception:
        log.exception("O agente estourou; seguindo pelo caminho antigo")
        return False
    finally:
        informar_interface("estado_voz", {"processando": False})

    if resultado.erro:
        log.warning("[%s] Agente sem resposta: %s", resultado.identificador, resultado.erro)
        return False

    if resultado.passos:
        log.info("[%s] Auditoria: %s", resultado.identificador, resultado.resumo_da_auditoria())

    # Ferramenta que mexe no computador do usuário: pergunta antes.
    if resultado.confirmacao is not None:
        ferramenta = registro_de_ferramentas().obter(resultado.confirmacao.nome)

        if ferramenta is None:
            return False

        ferramenta_pendente = resultado.confirmacao
        pedir_confirmacao(
            Intencao(
                nome="ferramenta_do_agente",
                confianca=0.5,
                argumento=resultado.confirmacao.nome,
                gatilho="agente",
                frase=pergunta,
            ),
            pergunta=(
                f"Você quer que eu "
                f"{ferramenta.pedido_de_confirmacao or ferramenta.descricao}?"
            ),
        )
        return True

    if resultado.texto:
        registrar_memoria("Jarvis", resultado.texto)

    # As ferramentas antigas já falam o resultado por conta própria; só é
    # preciso falar quando alguma delas só devolve informação (ou quando o
    # modelo não chamou ferramenta nenhuma e a resposta é a própria conversa).
    precisa_falar = not resultado.passos or not all(
        _ferramenta_fala_sozinha(passo.nome) for passo in resultado.passos
    )

    if precisa_falar and resultado.texto:
        falar(resultado.texto)

    if not resultado.texto and not resultado.passos:
        return False

    return True


def _ferramenta_fala_sozinha(nome):
    ferramenta = registro_de_ferramentas().obter(nome)
    return bool(ferramenta is None or ferramenta.fala_sozinha)


def executar_ferramenta_pendente():
    """Executa a ferramenta que estava esperando o "sim" do usuário."""

    global ferramenta_pendente

    chamada = ferramenta_pendente
    ferramenta_pendente = None

    if chamada is None:
        return True

    log.info("Usuário confirmou a ferramenta %s", chamada.nome)

    if chamada.nome == "sair_da_jarvis":
        falar("Até logo!")
        return False

    saida = executar_ferramenta_do_agente(chamada)
    log.info("Resultado da ferramenta confirmada: %s", saida)
    return True


def processar_comandos_interface():
    """Executa os comandos que o painel enfileirou. False manda encerrar."""

    for comando in ler_comandos_interface():
        log.info("Comando do painel: %r", comando)

        if not processar_comando(comando):
            return False

    return True


# ---------------------------------------------------------------------------
# Voz em segundo plano
# ---------------------------------------------------------------------------


def criar_detector_de_voz(config):
    """Traduz a configuração do painel para o detector de voz."""

    return DetectorDeVoz(
        limiar_minimo=float(config.get("limiar_minimo_de_voz", 45)),
        multiplicador_ruido=float(config.get("multiplicador_de_ruido", 1.8)),
        silencio_para_encerrar=float(config.get("silencio_para_encerrar", 1.0)),
    )


def atualizar_status_voz(forcar=False):
    """Manda para o painel o nível, o piso de ruído e o limiar atuais.

    É o que permite ver *por que* o Jarvis não está ouvindo, em vez de ficar
    só na frase "Aguardando você dizer: Jarvis".
    """

    global ultimo_status_voz

    if ouvinte is None:
        return

    agora = time.monotonic()

    if not forcar and agora - ultimo_status_voz < INTERVALO_DE_STATUS:
        return

    ultimo_status_voz = agora

    dados = ouvinte.estatisticas()
    dados["motor"] = gerenciador_voz.ultimo_motor if gerenciador_voz else ""
    dados["modo_offline"] = bool(gerenciador_voz and gerenciador_voz.em_modo_offline())
    dados["falando"] = esta_falando()
    informar_interface("estado_voz", dados)


def transcrever_frase(audio):
    """Transcreve tratando as falhas (com aviso limitado a um por minuto)."""

    if gerenciador_voz is None:
        return None

    resultado = gerenciador_voz.transcrever(audio)

    if resultado.ok:
        log.info("Reconhecido (%s): %r", resultado.motor, resultado.texto)
        return resultado.texto

    log.warning(
        "Não entendi a fala (%s). Motores tentados: %s",
        resultado.motivo or "sem detalhe",
        ", ".join(resultado.motores_tentados) or "nenhum",
    )

    if gerenciador_voz.deve_avisar_agora():
        mensagem = gerenciador_voz.mensagem_para_usuario(resultado)

        if mensagem:
            informar_interface(
                "aviso",
                {
                    "nivel": "erro",
                    "texto": mensagem,
                    "detalhe": resultado.motivo,
                    "motores": resultado.motores_tentados,
                },
            )
            falar(mensagem)

    return None


def aguardar_frase(tempo_maximo):
    """Espera a próxima frase pronta, sem travar a interface."""

    limite = time.monotonic() + tempo_maximo

    while programa_ativo and time.monotonic() < limite:
        if not processar_comandos_interface():
            return None

        if esta_falando():
            time.sleep(0.05)
            continue

        if ouvinte is None or not ouvinte.ativo:
            return None

        frase = ouvinte.retirar_frase(timeout=0.1)

        if frase is not None:
            return frase

        atualizar_status_voz()

    return None


def preparar_ouvinte(config):
    """Abre o microfone; se falhar, explica na tela em vez de ficar mudo."""

    global ouvinte

    ouvinte = ThreadOuvinte(
        config=config,
        detector=criar_detector_de_voz(config),
        logger=log,
    )

    if ouvinte.iniciar():
        log.info("Microfone em uso: %s", ouvinte.nome_microfone or "padrão do sistema")
        informar_interface(
            "aviso",
            {
                "nivel": "info",
                "texto": f"Microfone em uso: {ouvinte.nome_microfone or 'padrão do sistema'}",
            },
        )
        return True

    mensagem = ouvinte.ultimo_erro or "erro desconhecido"
    log.error("Não consegui abrir o microfone: %s", mensagem)
    informar_interface(
        "aviso",
        {
            "nivel": "erro",
            "texto": "Não consegui abrir o microfone.",
            "detalhe": mensagem,
        },
    )
    falar("Não consegui abrir o microfone. Veja o painel para mais detalhes.")
    return False


def executar():
    """Laço principal: atende o painel, escuta e executa."""

    global programa_ativo, modo_conversa_ate, gerenciador_voz, DPI_DECLARADO

    DPI_DECLARADO = configurar_escalas_de_video()

    config = carregar_config()
    gerenciador_voz = GerenciadorDeVoz(config=config, logger=log)

    falar("Jarvis iniciada. Diga Jarvis para falar comigo.")

    if not preparar_ouvinte(config):
        # Continua rodando: dá para usar o painel e tentar reabrir o microfone.
        pass

    programa_ativo = True
    modo_conversa_ate = 0.0
    ultima_tentativa_de_microfone = time.monotonic()

    while programa_ativo:
        try:
            if not processar_comandos_interface():
                break

            agora = time.monotonic()

            if esta_falando():
                if ouvinte is not None:
                    ouvinte.pausar()
                time.sleep(0.05)
                continue

            if tempo_ultima_fala is not None and agora < tempo_ultima_fala + TEMPO_COOLDOWN_FALA:
                time.sleep(0.05)
                continue

            if ouvinte is not None and ouvinte.pausado:
                ouvinte.retomar()

            if ouvinte is None or not ouvinte.ativo:
                if agora - ultima_tentativa_de_microfone > 10:
                    ultima_tentativa_de_microfone = agora
                    log.info("Tentando reabrir o microfone...")
                    if preparar_ouvinte(config):
                        ultima_tentativa_de_microfone = agora
                atualizar_status_voz()
                time.sleep(0.2)
                continue

            audio = ouvinte.retirar_frase(timeout=0.1)

            if audio is None:
                atualizar_status_voz()
                continue

            texto = transcrever_frase(audio)

            if not texto:
                atualizar_status_voz()
                continue

            em_conversa = agora < modo_conversa_ate
            extraido = extrair_comando_apos_ativacao(texto)

            if extraido is None:
                if not em_conversa:
                    log.debug("Frase ignorada (sem a palavra Jarvis): %r", texto)
                    continue
                comando = texto
            else:
                comando = extraido

            if not comando.strip():
                falar("Sim?")

                audio = aguardar_frase(TEMPO_DE_ESPERA_POR_RESPOSTA)

                if audio is None:
                    continue

                texto = transcrever_frase(audio)

                if not texto:
                    continue

                comando = texto

            programa_ativo = processar_comando(comando)
            modo_conversa_ate = time.monotonic() + SESSAO_CONVERSA

        except KeyboardInterrupt:
            log.info("Encerrada por Ctrl+C")
            break
        except Exception:
            log.exception("Erro no laço principal")
            time.sleep(1)

    if ouvinte is not None:
        ouvinte.parar()

    log.info("Jarvis encerrada.")


if __name__ == "__main__":
    executar()
