import base64
import ctypes
import difflib
import json
import os
import queue
import shutil
import subprocess
import time
import unicodedata
import webbrowser

from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

import mss
import numpy as np
import pyautogui
import pyperclip
import pytesseract
import sounddevice as sd
import speech_recognition as sr

from PIL import Image
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


mutex = ctypes.windll.kernel32.CreateMutexW(
    None,
    False,
    "JarvisAssistenteDeVozUnica"
)

if ctypes.windll.kernel32.GetLastError() == 183:
    print("A Jarvis já está aberta. Feche a outra janela antes de iniciar novamente.")
    raise SystemExit


TAXA_DE_AUDIO = 16000
LIMIAR_MINIMO_DE_VOZ = 120
MULTIPLICADOR_DE_RUIDO = 1.8
SILENCIO_PARA_ENCERRAR = 1.1
TEMPO_MAXIMO_DE_FALA = 20
TEMPO_MAXIMO_DE_ESPERA = 60
SESSAO_CONVERSA = 30
TEMPO_COOLDOWN_FALA = 0.8

PASTA = Path(__file__).resolve().parent
ARQUIVO_MEMORIA = PASTA / "memoria_jarvis.json"
ARQUIVO_CONFIG = PASTA / "config_jarvis.json"
ARQUIVO_COMANDOS_APRENDIDOS = PASTA / "comandos_aprendidos.json"
ARQUIVO_COMANDOS_INTERFACE = PASTA / "comandos_interface.json"
ARQUIVO_EVENTOS_INTERFACE = PASTA / "eventos_interface.json"

CAMINHO_TESSERACT = shutil.which("tesseract") or r"C:\Program Files\Tesseract-OCR\tesseract.exe"

pytesseract.pytesseract.tesseract_cmd = CAMINHO_TESSERACT

reconhecedor = sr.Recognizer()
reconhecedor.pause_threshold = 1.2
reconhecedor.non_speaking_duration = 0.4
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODELO_LOCAL = "qwen2.5:3b"
CAMINHO_OLLAMA = Path(
    os.environ.get(
        "OLLAMA_EXECUTABLE",
        r"C:\Users\Matheus\AppData\Local\Programs\Ollama\ollama.exe"
    )
)

clique_pendente = None
processo_fala = None
voz_windows = None
voz_windows_carregada = False


def carregar_config():
    padrao = {
        "visualizacao_de_tela_ativada": False,
        "filtro_palavroes_ativado": False
    }

    try:
        if ARQUIVO_CONFIG.exists():
            dados = json.loads(ARQUIVO_CONFIG.read_text(encoding="utf-8"))

            if isinstance(dados, dict):
                padrao.update(dados)
    except (json.JSONDecodeError, OSError):
        pass

    return padrao


def tela_permitida():
    return carregar_config()["visualizacao_de_tela_ativada"]


def filtro_de_palavroes_ativado():
    return carregar_config()["filtro_palavroes_ativado"]


def carregar_memoria():
    if not ARQUIVO_MEMORIA.exists():
        return []

    try:
        dados = json.loads(ARQUIVO_MEMORIA.read_text(encoding="utf-8"))
        if not isinstance(dados, list):
            return []
        return [item for item in dados if isinstance(item, dict)]
    except (json.JSONDecodeError, OSError):
        return []


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
    ARQUIVO_COMANDOS_APRENDIDOS.write_text(
        json.dumps(comandos_aprendidos, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


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
    memoria.append({
        "data": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "tipo": tipo,
        "texto": texto
    })

    ARQUIVO_MEMORIA.write_text(
        json.dumps(memoria, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def ler_comandos_interface():
    if not ARQUIVO_COMANDOS_INTERFACE.exists():
        return []

    try:
        comandos = json.loads(
            ARQUIVO_COMANDOS_INTERFACE.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(comandos, list):
        return []

    try:
        ARQUIVO_COMANDOS_INTERFACE.write_text("[]", encoding="utf-8")
    except OSError:
        pass

    return [
        item.get("texto", "").strip()
        for item in comandos
        if isinstance(item, dict) and item.get("texto", "").strip()
    ]


def registrar_evento_interface(tipo, dados=None):
    eventos = []

    if ARQUIVO_EVENTOS_INTERFACE.exists():
        try:
            conteudo = json.loads(
                ARQUIVO_EVENTOS_INTERFACE.read_text(encoding="utf-8")
            )
            if isinstance(conteudo, list):
                eventos = conteudo
        except (json.JSONDecodeError, OSError):
            pass

    eventos.append({"tipo": tipo, "dados": dados or {}})
    ARQUIVO_EVENTOS_INTERFACE.write_text(
        json.dumps(eventos, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def navegar_no_painel(url):
    registrar_evento_interface("navegar_navegador", {"url": url})


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
    global processo_fala, tempo_ultima_fala

    print(f"Jarvis: {texto}")
    registrar_memoria("Jarvis", texto)
    tempo_ultima_fala = time.monotonic()

    voz = selecionar_voz_windows()

    comando_powershell = (
        "Add-Type -AssemblyName System.Speech; "
        "$voz = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$voz.Rate = 1; $voz.Volume = 100; "
        + (f"$voz.SelectVoice('{voz}'); " if voz else "")
        + f"$voz.Speak({json.dumps(texto, ensure_ascii=False)})"
    )
    comando_codificado = base64.b64encode(
        comando_powershell.encode("utf-16le")
    ).decode("ascii")

    processo_fala = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-EncodedCommand", comando_codificado],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def normalizar(texto):
    texto = unicodedata.normalize("NFD", texto.lower())

    texto = "".join(
        letra for letra in texto
        if unicodedata.category(letra) != "Mn"
    )

    texto = "".join(
        letra if letra.isalnum() else " "
        for letra in texto
    )

    return " ".join(texto.split())


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


def imagem_para_base64(imagem):
    memoria_imagem = BytesIO()
    imagem.save(memoria_imagem, format="PNG")

    return base64.b64encode(
        memoria_imagem.getvalue()
    ).decode("utf-8")


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

        clique_pendente = {
            "texto": texto_procurado,
            "x": posicao["x"],
            "y": posicao["y"]
        }

        falar(
            f"Encontrei {texto_procurado}. "
            "Diga confirmar clique para eu clicar."
        )

    except Exception:
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
        falar("Não consegui realizar o clique.")


def cancelar_clique():
    global clique_pendente

    clique_pendente = None
    falar("Clique cancelado.")


def iniciar_ollama_se_necessario():
    if not CAMINHO_OLLAMA.exists():
        raise RuntimeError("O executável do Ollama não foi encontrado.")

    subprocess.Popen(
        [str(CAMINHO_OLLAMA), "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW
    )


def perguntar_ao_ollama(instrucao):
    dados = json.dumps({
        "model": MODELO_LOCAL,
        "prompt": instrucao,
        "stream": False,
        "options": {
            "temperature": 0.4,
            "num_predict": 256
        }
    }).encode("utf-8")

    for tentativa in range(2):
        requisicao = Request(
            OLLAMA_URL,
            data=dados,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        try:
            with urlopen(requisicao, timeout=120) as resposta:
                resultado = json.loads(resposta.read().decode("utf-8"))
            return resultado.get("response", "").strip()
        except Exception:
            if tentativa == 0:
                iniciar_ollama_se_necessario()
                time.sleep(2)

    raise RuntimeError("O Ollama não respondeu.")


def responder_com_ollama(pergunta):
    print("Jarvis: Pensando...")

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
        falar(
            "Não consegui acessar a IA local. Verifique se o Ollama está instalado."
        )


def listar_aplicativos():
    comando = (
        "Get-StartApps | "
        "Select-Object Name, AppID | "
        "ConvertTo-Json -Compress"
    )

    resultado = subprocess.run(
        ["powershell", "-NoProfile", "-Command", comando],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    if not resultado.stdout.strip():
        return {}

    try:
        dados = json.loads(resultado.stdout)
    except json.JSONDecodeError:
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

            subprocess.Popen(
                ["explorer.exe", f"shell:AppsFolder\\{app_id}"]
            )

            falar(f"Abrindo {nome_original}.")
            return

    parecidos = difflib.get_close_matches(
        nome,
        aplicativos.keys(),
        n=1,
        cutoff=0.78
    )

    if parecidos:
        nome_original, app_id = aplicativos[parecidos[0]]

        subprocess.Popen(
            ["explorer.exe", f"shell:AppsFolder\\{app_id}"]
        )

        falar(f"Abrindo {nome_original}.")
    else:
        falar(f"Não encontrei o aplicativo {nome_falado}.")


def pesquisar_na_internet(comando):
    pesquisa = comando

    for inicio in [
        "pesquisar por",
        "pesquisar",
        "pesquise por",
        "pesquise",
        "procure por",
        "procura por",
        "buscar por",
        "buscar"
    ]:
        if pesquisa.startswith(inicio):
            pesquisa = pesquisa.replace(inicio, "", 1).strip()
            break

    pesquisa = pesquisa.replace("na internet", "").strip()

    if not pesquisa:
        falar("Diga o que você quer pesquisar.")
        return

    link = "https://www.google.com/search?q=" + quote_plus(pesquisa)
    navegar_no_painel(link)
    falar(f"Pesquisando por {pesquisa}.")


def tocar_no_youtube(comando):
    musica = comando

    for trecho in [
        "tocar", "toque", "colocar", "coloque",
        "no youtube", "no you tube",
        "youtube", "you tube"
    ]:
        musica = musica.replace(trecho, "")

    musica = musica.strip()

    if not musica:
        falar("Diga qual música você quer tocar.")
        return

    link = "https://www.youtube.com/results?search_query=" + quote_plus(musica)
    navegar_no_painel(link)
    falar(f"Abrindo {musica} no YouTube.")


def abrir_automacao_youtube_music():
    chrome_options = Options()
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-popup-blocking")
    chrome_options.add_argument("--user-agent=Mozilla/5.0")

    try:
        return webdriver.Chrome(options=chrome_options)
    except Exception:
        return None


def tocar_no_youtube_music(comando):
    musica = comando

    for trecho in [
        "tocar", "toque", "colocar", "coloque",
        "no youtube music", "no you tube music",
        "youtube music", "you tube music",
        "na musica", "na música",
        "musica do youtube", "música do youtube"
    ]:
        musica = musica.replace(trecho, "")

    musica = musica.strip()

    if not musica:
        falar("Diga qual música você quer tocar.")
        return

    link = "https://music.youtube.com/search?q=" + quote_plus(musica)

    navegar_no_painel(link)
    falar(f"Abrindo {musica} no YouTube Music.")


def enviar_tecla_de_midia(codigo_tecla):
    ctypes.windll.user32.keybd_event(codigo_tecla, 0, 0, 0)
    ctypes.windll.user32.keybd_event(codigo_tecla, 0, 2, 0)


def controlar_musica(comando_normalizado):
    if any(texto in comando_normalizado for texto in [
        "pausar", "pause", "parar musica", "parar a musica"
    ]):
        enviar_tecla_de_midia(0xB3)
        falar("Pausando a música.")
        return True

    if any(texto in comando_normalizado for texto in [
        "continuar musica", "continuar a musica", "reproduzir musica",
        "dar play", "play"
    ]):
        enviar_tecla_de_midia(0xB3)
        falar("Reproduzindo a música.")
        return True

    if any(texto in comando_normalizado for texto in [
        "proxima musica", "proxima faixa", "pular musica", "pular faixa"
    ]):
        enviar_tecla_de_midia(0xB0)
        falar("Próxima música.")
        return True

    if any(texto in comando_normalizado for texto in [
        "musica anterior", "faixa anterior", "voltar musica"
    ]):
        enviar_tecla_de_midia(0xB1)
        falar("Música anterior.")
        return True

    if "aumentar volume" in comando_normalizado or "aumente o volume" in comando_normalizado:
        enviar_tecla_de_midia(0xAF)
        falar("Aumentando o volume.")
        return True

    if "diminuir volume" in comando_normalizado or "diminua o volume" in comando_normalizado:
        enviar_tecla_de_midia(0xAE)
        falar("Diminuindo o volume.")
        return True

    if "silenciar" in comando_normalizado or "mutar" in comando_normalizado:
        enviar_tecla_de_midia(0xAD)
        falar("Áudio alternado.")
        return True

    return False


def corrigir_texto_copiado(colar_depois=False):
    texto_original = pyperclip.paste().strip()

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

        pyperclip.copy(texto_corrigido)

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
        falar("Não consegui corrigir o texto agora.")


def selecionar_dispositivo_microfone():
    try:
        dispositivos = sd.query_devices()
    except Exception:
        return None

    if not isinstance(dispositivos, (list, tuple)):
        return None

    for indice, item in enumerate(dispositivos):
        nome = ""

        if isinstance(item, tuple):
            if len(item) >= 2:
                nome = str(item[1])
        elif isinstance(item, dict):
            nome = str(item.get("name", ""))
        else:
            nome = str(item)

        texto = nome.lower()
        if (
            "microphone" in texto or
            "microfone" in texto or
            "mic" in texto or
            "array" in texto
        ) and (
            "mapeador" not in texto and
            "mapper" not in texto and
            "output" not in texto and
            "speaker" not in texto and
            "alto-falante" not in texto
        ):
            return indice

    try:
        return int(sd.default.device[0])
    except Exception:
        return None


def ouvir_ate_silencio(tempo_maximo_espera=TEMPO_MAXIMO_DE_ESPERA):
    fila_de_audio = queue.Queue()
    dispositivo = selecionar_dispositivo_microfone()
    inicio_espera = time.monotonic()
    blocos_anteriores = []
    blocos_gravados = []
    iniciou_fala = False
    inicio_fala = 0.0
    ultimo_bloco_com_voz = 0.0
    blocos_com_voz = 0
    nivel_ruido = float(LIMIAR_MINIMO_DE_VOZ / MULTIPLICADOR_DE_RUIDO)

    def receber_audio(indata, frames, tempo, status):
        if status:
            print(f"Jarvis: áudio: {status}")
        fila_de_audio.put(indata.copy())

    parametros_audio = {
        "samplerate": TAXA_DE_AUDIO,
        "channels": 1,
        "dtype": "int16",
        "blocksize": 1024,
        "callback": receber_audio
    }

    if dispositivo is not None:
        parametros_audio["device"] = dispositivo

    try:
        with sd.InputStream(**parametros_audio):
            while True:
                agora = time.monotonic()
                tempo_restante = tempo_maximo_espera - (agora - inicio_espera)

                if tempo_restante <= 0 and not iniciou_fala:
                    return None

                try:
                    bloco = fila_de_audio.get(timeout=0.2)
                except queue.Empty:
                    continue

                amostras = bloco.astype(np.float32).reshape(-1)
                nivel = float(np.sqrt(np.mean(amostras * amostras)))

                if not iniciou_fala:
                    blocos_anteriores.append(bloco)
                    blocos_anteriores = blocos_anteriores[-8:]

                    if agora - inicio_espera <= 1.0:
                        nivel_ruido = min(
                            nivel_ruido * 1.08 + nivel * 0.12,
                            max(nivel, nivel_ruido)
                        )

                    limiar = max(
                        LIMIAR_MINIMO_DE_VOZ,
                        nivel_ruido * MULTIPLICADOR_DE_RUIDO
                    )

                    if nivel > limiar:
                        blocos_com_voz += 1
                    else:
                        blocos_com_voz = 0

                    if blocos_com_voz >= 2:
                        iniciou_fala = True
                        inicio_fala = agora
                        ultimo_bloco_com_voz = agora
                        blocos_gravados.extend(blocos_anteriores)
                        blocos_anteriores = []

                    continue

                blocos_gravados.append(bloco)
                limiar = max(
                    LIMIAR_MINIMO_DE_VOZ,
                    nivel_ruido * MULTIPLICADOR_DE_RUIDO
                )

                if nivel > limiar:
                    ultimo_bloco_com_voz = agora

                terminou_por_silencio = (
                    agora - ultimo_bloco_com_voz >= SILENCIO_PARA_ENCERRAR and
                    agora - inicio_fala >= 0.45
                )
                terminou_por_tempo = agora - inicio_fala >= TEMPO_MAXIMO_DE_FALA

                if terminou_por_silencio or terminou_por_tempo:
                    break
    except Exception as erro_audio:
        print(f"Jarvis: erro ao capturar o microfone: {erro_audio}")
        return None

    if not blocos_gravados:
        return None

    return np.concatenate(blocos_gravados)


def transformar_audio_em_texto(audio_gravado):
    audio = sr.AudioData(
        audio_gravado.tobytes(),
        TAXA_DE_AUDIO,
        2
    )

    resultado = reconhecedor.recognize_google(
        audio,
        language="pt-BR",
        show_all=True
    )

    if not resultado or not resultado.get("alternative"):
        raise sr.UnknownValueError()

    candidatos = [
        item.get("transcript", "").strip().lower()
        for item in resultado["alternative"]
        if item.get("transcript", "").strip()
    ]

    palavras_prioritarias = (
        "jarvis", "jarves", "jarviz", "jarvi", "javis",
        "tocar", "toque", "colocar", "coloque", "abrir",
        "pesquisar", "pesquise", "procure", "buscar", "corrigir"
    )

    candidatos.sort(
        key=lambda texto: sum(
            palavra in normalizar(texto)
            for palavra in palavras_prioritarias
        ),
        reverse=True
    )
    return candidatos[0]


def processar_comando(comando):
    global clique_pendente

    comando = comando.strip()
    comando_normalizado = normalizar(comando)

    registrar_memoria("Usuário", comando)

    if comando_normalizado.startswith("quando eu disser ") or comando_normalizado.startswith("aprenda que "):
        aprender_comando(comando_normalizado)
        return True

    acao_aprendida = comandos_aprendidos.get(comando_normalizado)
    if acao_aprendida and acao_aprendida != comando_normalizado:
        falar(f"Executando o comando aprendido: {comando_normalizado}.")
        return processar_comando(acao_aprendida)

    if comando_normalizado in ["sair", "fechar jarvis", "encerrar jarvis"]:
        falar("Até logo!")
        return False

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
        "não",
        "cancelar",
        "cancelar clique"
    ]:
        cancelar_clique()
        return True

    if (
        "ler minha tela" in comando_normalizado or
        "descrever minha tela" in comando_normalizado or
        "descreva minha tela" in comando_normalizado or
        "o que tem na minha tela" in comando_normalizado
    ):
        descrever_tela_com_ollama()
        return True

    if comando_normalizado.startswith("clicar em "):
        texto = comando_normalizado.replace("clicar em ", "", 1).strip()
        preparar_clique(texto)
        return True

    if "que horas" in comando_normalizado or comando_normalizado == "hora":
        hora = datetime.now().strftime("%H:%M")
        falar(f"Agora são {hora}.")
        return True

    if "corrigir e colar" in comando_normalizado or "corrija e cole" in comando_normalizado:
        corrigir_texto_copiado(colar_depois=True)
        return True

    if "corrigir texto copiado" in comando_normalizado or "corrija texto copiado" in comando_normalizado:
        corrigir_texto_copiado(colar_depois=False)
        return True

    if controlar_musica(comando_normalizado):
        return True

    if comando_normalizado in ["abrir youtube music", "abrir youtube musica"]:
        navegar_no_painel("https://music.youtube.com")
        falar("Abrindo o YouTube Music.")
        return True

    comandos_musica = ("tocar", "toque", "colocar", "coloque")
    if (
        any(comando_normalizado.startswith(item) for item in comandos_musica) and
        ("youtube music" in comando_normalizado or "you tube music" in comando_normalizado or
         "musica" in comando_normalizado)
    ):
        tocar_no_youtube_music(comando_normalizado)
        return True

    if (
        any(comando_normalizado.startswith(item) for item in comandos_musica) and
        ("youtube" in comando_normalizado or "you tube" in comando_normalizado)
    ):
        tocar_no_youtube(comando_normalizado)
        return True

    if comando_normalizado in ["abrir navegador", "abrir o navegador"]:
        webbrowser.open("https://www.google.com")
        falar("Abrindo o navegador.")
        return True

    if (
        comando_normalizado.startswith("pesquisar") or
        comando_normalizado.startswith("pesquise") or
        comando_normalizado.startswith("procure") or
        comando_normalizado.startswith("buscar")
    ):
        pesquisar_na_internet(comando_normalizado)
        return True

    if comando_normalizado.startswith("abrir "):
        nome_aplicativo = comando_normalizado.replace("abrir ", "", 1).strip()
        abrir_aplicativo(nome_aplicativo)
        return True

    responder_com_ollama(comando)
    return True


def processar_comandos_interface():
    for comando in ler_comandos_interface():
        processar_comando(comando)


tempo_ultima_fala = None

falar("Jarvis iniciada. Diga Jarvis para falar comigo.")

programa_ativo = True
modo_conversa_ate = 0

while programa_ativo:
    try:
        processar_comandos_interface()
        agora = time.monotonic()

        if processo_fala is not None and processo_fala.poll() is None:
            time.sleep(0.1)
            continue

        if tempo_ultima_fala is not None and agora < tempo_ultima_fala + TEMPO_COOLDOWN_FALA:
            time.sleep(0.25)
            continue

        if agora < modo_conversa_ate:
            audio_comando = ouvir_ate_silencio(
                tempo_maximo_espera=modo_conversa_ate - agora
            )

            if audio_comando is None:
                continue

            comando = transformar_audio_em_texto(audio_comando)
            print(f"Você disse: {comando}")

            comando_com_jarvis = extrair_comando_apos_ativacao(comando)

            if comando_com_jarvis is not None:
                comando = comando_com_jarvis

            if not comando:
                falar("Sim?")
                continue

            programa_ativo = processar_comando(comando)
            modo_conversa_ate = time.monotonic() + SESSAO_CONVERSA
            continue

        print("\nAguardando você dizer: Jarvis")

        audio_ativacao = ouvir_ate_silencio()

        if audio_ativacao is None:
            continue

        frase = transformar_audio_em_texto(audio_ativacao)
        print(f"Você disse: {frase}")

        comando = extrair_comando_apos_ativacao(frase)

        if comando is None:
            print("Frase ignorada. Aguardando Jarvis...")
            continue

        if not comando:
            falar("Sim?")

            audio_comando = ouvir_ate_silencio()

            if audio_comando is None:
                continue

            comando = transformar_audio_em_texto(audio_comando)
            print(f"Você disse: {comando}")

        programa_ativo = processar_comando(comando)
        modo_conversa_ate = time.monotonic() + SESSAO_CONVERSA

    except sr.UnknownValueError:
        print("Jarvis: Não consegui entender. Tente novamente.")

    except sr.RequestError:
        falar("Erro ao acessar o reconhecimento de voz.")

    except Exception as erro:
        print(f"Jarvis: Erro no loop de áudio: {erro}")
        time.sleep(1)

    except KeyboardInterrupt:
        print("\nJarvis encerrada.")
        break