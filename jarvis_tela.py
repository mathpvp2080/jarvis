import ctypes
import html
import math
import os
import sys
import subprocess
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

import cv2
import psutil
from mediapipe import Image as MpImage, ImageFormat as MpImageFormat
from mediapipe.tasks.python import BaseOptions, vision

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView

from jarvis_comum import (
    configurar_log,
    consumir_eventos,
    escrever_json_atomico,
    ler_json,
    ler_jsonl,
    publicar_comando,
    registrar_excecoes_nao_tratadas,
)
from jarvis_voz import GerenciadorDeVoz

PASTA = Path(__file__).resolve().parent
ARQUIVO_MEMORIA = PASTA / "memoria_jarvis.jsonl"
ARQUIVO_MEMORIA_LEGADO = PASTA / "memoria_jarvis.json"
ARQUIVO_CONFIG = PASTA / "config_jarvis.json"

log = configurar_log("jarvis.tela")

# Avisos que o hook de exceções deixa aqui e o painel mostra (com limite de
# frequência), em vez de sumirem num console que ninguém vê.
AVISOS_PENDENTES: deque = deque(maxlen=20)


def registrar_aviso(mensagem: str) -> None:
    AVISOS_PENDENTES.append(str(mensagem))


registrar_excecoes_nao_tratadas(log, ao_avisar=registrar_aviso)


# Teclas usadas pelos gestos (códigos virtuais do Windows).
CODIGOS_DE_TECLA = {
    "PGUP": 0x21,
    "PGDN": 0x22,
    "SPACE": 0x20,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    "ALT": 0x12,
}

_CONFIG_EM_CACHE = {"assinatura": None, "dados": None}


def carregar_config():
    """Configuração do painel.

    ``bloquear_ao_sair`` passou a vir **desligado**: com câmera instável,
    o computador podia bloquear sozinho enquanto a pessoa trabalhava.
    """

    padrao = {
        "visualizacao_de_tela_ativada": False,
        "filtro_palavroes_ativado": False,
        "camera_ativada": True,
        "bloquear_ao_sair": False,
        "detectar_emocoes": True,
        "exoesqueleto_facial": True,
        "holograma_ativado": True,
        "reagir_a_gestos": True,
        "microfone": "",
        "motor_de_voz": "",
        "modelo_whisper": "small",
        "pasta_modelo_vosk": "",
        "limiar_minimo_de_voz": 45,
        "multiplicador_de_ruido": 1.8,
        "silencio_para_encerrar": 1.0,
    }

    try:
        estado = ARQUIVO_CONFIG.stat()
        assinatura = (estado.st_mtime_ns, estado.st_size)
    except OSError:
        assinatura = None

    # O timer da câmera chama isto várias vezes por segundo; sem cache, o
    # arquivo era lido e reprocessado a cada 220 ms.
    if _CONFIG_EM_CACHE["assinatura"] == assinatura and _CONFIG_EM_CACHE["dados"] is not None:
        return dict(_CONFIG_EM_CACHE["dados"])

    dados = ler_json(ARQUIVO_CONFIG, {})
    if isinstance(dados, dict):
        padrao.update(dados)

    _CONFIG_EM_CACHE["assinatura"] = assinatura
    _CONFIG_EM_CACHE["dados"] = padrao

    return dict(padrao)


def salvar_config(config):
    if not escrever_json_atomico(ARQUIVO_CONFIG, config):
        log.error("Falha ao salvar as configurações em %s", ARQUIVO_CONFIG)


def preparar_modelos_da_camera():
    """Garante os modelos do mediapipe sem derrubar a interface se faltarem.

    Antes, um modelo ausente fazia a interface morrer no startup com
    traceback de mediapipe — sem nenhuma pista do que fazer.
    """

    try:
        from preparar_modelos import garantir_modelos

        return garantir_modelos(PASTA, baixar=True, log=log.info)
    except Exception:
        log.exception("Não consegui preparar os modelos da câmera")
        return ["blaze_face_short_range.tflite", "face_landmarker.task", "hand_landmarker.task"]


def criar_cartao(titulo):
    cartao = QFrame()
    cartao.setObjectName("cartao")

    layout = QVBoxLayout(cartao)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(10)

    texto_titulo = QLabel(titulo)
    texto_titulo.setObjectName("tituloCartao")

    layout.addWidget(texto_titulo)

    return cartao, layout


class NucleoJarvis(QWidget):
    def __init__(self):
        super().__init__()

        self.fase = 0
        self.setMinimumSize(430, 430)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animar)
        self.timer.start(40)

    def animar(self):
        self.fase += 0.06
        self.update()

    def paintEvent(self, evento):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        centro_x = self.width() / 2
        centro_y = self.height() / 2

        pulso = 8 * math.sin(self.fase)
        rotacao = self.fase * 25

        painter.translate(centro_x, centro_y)
        painter.rotate(rotacao)

        for raio, largura, opacidade in [
            (145 + pulso, 2, 80),
            (120 - pulso, 3, 110),
            (92 + pulso, 3, 150)
        ]:
            cor = QColor(0, 183, 255, opacidade)
            painter.setPen(QPen(cor, largura))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(
                int(-raio),
                int(-raio),
                int(raio * 2),
                int(raio * 2)
            )

        painter.rotate(-rotacao)

        painter.setPen(QPen(QColor(0, 213, 255, 220), 4))
        painter.setBrush(QColor(13, 55, 82, 240))
        painter.drawEllipse(-64, -64, 128, 128)

        painter.setBrush(QColor(0, 167, 230, 190))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(-42, -42, 84, 84)

        painter.setPen(QPen(QColor(97, 240, 255), 5))

        for x in [-20, -7, 7, 20]:
            altura = 8 + int(8 * abs(math.sin(self.fase + x)))
            painter.drawLine(x, -altura, x, altura)


class JanelaJarvis(QMainWindow):
    def __init__(self):
        super().__init__()

        self.inicio = datetime.now()
        self.ultimo_html_chat = ""
        self.camera = None

        # Modelos da câmera: garantidos aqui, com aviso claro se faltarem.
        self.modelos_faltando = preparar_modelos_da_camera()
        self.detector_rosto = None
        self.detector_expressoes = None
        self.detector_maos = None
        self.criar_detectores()

        # Modelos pesados rodam em cadências diferentes (ver _atualizar_camera):
        # o de expressões é o mais caro e não precisa de 4,5 análises por
        # segundo. O último resultado fica guardado para desenhar nos quadros
        # intermediários, então nada pisca.
        self.contador_de_frames = 0
        self.cadencia_de_expressoes = 3
        self._ultima_expressao = None

        # Estado de proteção dos recursos (evita fechar detector duas vezes).
        self.recursos_liberados = False
        self.encerrando = False
        self.ultimo_frame_ok = 0.0
        self.erro_na_camera_ate = 0.0

        # Bloqueio por ausência: agenda antes de bloquear (dá tempo de voltar).
        self.bloqueio_agendado_para = 0.0
        self.aviso_de_bloqueio_dado = False

        self.ultimo_bloqueio = 0
        self.ultimo_rosto = None
        self.rosto_confirmado = False
        self.emocao_atual = "indisponível"
        self.camera_expandida = False
        self.ultimo_estado_voz = {}
        self.janela_camera_cheia = None
        self.camera_preview_cheia = None
        self.janela_navegador_expandida = None
        self.janela_chat_expandida = None
        self.perfil_navegador = QWebEngineProfile("JarvisBrowser", self)
        pasta_navegador = PASTA / "dados_navegador"
        pasta_navegador.mkdir(exist_ok=True)
        self.perfil_navegador.setPersistentStoragePath(str(pasta_navegador))
        self.perfil_navegador.setCachePath(str(pasta_navegador / "cache"))
        self.perfil_navegador.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        self.gesto_atual = "nenhum"
        self.ultimo_gesto = ""
        self.ultimo_gesto_tempo = 0
        self.holograma_rotacao_x = 0.0
        self.holograma_rotacao_y = 0.0
        self.holograma_escala = 1.0
        self.mao_segura_holograma = False
        self.centro_mao_anterior = None
        self.distancia_mao_anterior = None
        self.holograma_visivel = False
        self.ultimo_comando_mao = "nenhum"
        self.centro_mao_comando = None
        self.movimento_acumulado_x = 0.0
        self.movimento_acumulado_y = 0.0
        self.ultimo_tempo_comando_mao = 0.0

        self.setWindowTitle("J.A.R.V.I.S")
        self.setMinimumSize(1200, 760)

        self.setStyleSheet("""
            QMainWindow {
                background-color: #050B11;
                color: #D8F6FF;
                font-family: Segoe UI;
            }

            QDialog {
                background-color: #071923;
                color: #D8F6FF;
            }

            QLabel {
                color: #D8F6FF;
            }

            QFrame#topo {
                background-color: #07121C;
                border-bottom: 1px solid #173448;
            }

            QFrame#cartao {
                background-color: #071923;
                border: 1px solid #123649;
                border-radius: 14px;
            }

            QLabel#tituloCartao {
                color: #8EEBFF;
                font-size: 15px;
                font-weight: bold;
            }

            QLabel#valorGrande {
                color: #DDF9FF;
                font-size: 28px;
                font-weight: bold;
            }

            QLabel#textoPequeno {
                color: #83AAB8;
                font-size: 12px;
            }

            QLabel#marca {
                color: #5AE7FF;
                font-size: 22px;
                font-weight: bold;
                letter-spacing: 5px;
            }

            QLabel#online {
                color: #4BFF9A;
                font-size: 12px;
                font-weight: bold;
            }

            QPlainTextEdit, QTextBrowser, QLineEdit {
                background-color: #06151E;
                color: #DDF9FF;
                border: none;
                border-radius: 10px;
                padding: 12px;
                font-size: 13px;
            }

            QTextBrowser {
                selection-background-color: #1B6178;
            }

            QTextBrowser .mensagem {
                padding: 10px;
                margin: 6px;
                border-radius: 10px;
            }

            QTextBrowser .usuario {
                background-color: #0B4054;
            }

            QTextBrowser .jarvis {
                background-color: #102A34;
            }

            QTextBrowser small {
                color: #78A9B7;
                margin-left: 8px;
            }

            QPushButton {
                background-color: #0B2A3A;
                color: #A8F2FF;
                border: 1px solid #1C607A;
                border-radius: 9px;
                min-height: 28px;
                max-height: 32px;
                padding: 4px 9px;
                font-weight: bold;
                font-size: 11px;
            }

            QPushButton:hover {
                background-color: #0D4258;
                border: 1px solid #44DFFF;
            }

            QPushButton#botaoAcao {
                min-width: 34px;
                max-width: 118px;
            }

            QPushButton#botaoNavegador {
                min-width: 34px;
                max-width: 42px;
                font-size: 15px;
                padding: 2px 6px;
            }

            QPushButton#botaoDestaque {
                background-color: #0E5368;
                border-color: #41D8F4;
            }

            QTabWidget::pane {
                border: 1px solid #123649;
                background-color: #06151E;
            }

            QTabBar::tab {
                background-color: #071923;
                color: #83AAB8;
                padding: 6px 12px;
                min-width: 90px;
            }

            QTabBar::tab:selected {
                color: #DDF9FF;
                background-color: #0B4054;
            }

            QCheckBox {
                color: #D8F6FF;
                padding: 7px;
            }
        """)

        self.montar_interface()
        self.criar_icone_tray()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.atualizar_painel)
        self.timer.start(1000)

        self.timer_camera = QTimer(self)
        self.timer_camera.timeout.connect(self.atualizar_camera)
        self.timer_camera.start(220)

        self.atualizar_painel()

    def criar_detectores(self):
        """Cria os detectores do mediapipe, um a um, sem derrubar a interface.

        Cada modelo sustenta um recurso diferente: sem o de mãos não há gestos
        nem holograma; sem o de expressões não há emoções. O que faltar é
        informado na tela.
        """

        if self.modelos_faltando:
            log.warning(
                "Modelos ausentes (%s): recursos de câmera reduzidos",
                ", ".join(self.modelos_faltando),
            )

        if "blaze_face_short_range.tflite" not in self.modelos_faltando:
            try:
                self.detector_rosto = vision.FaceDetector.create_from_options(
                    vision.FaceDetectorOptions(
                        base_options=BaseOptions(
                            model_asset_path=str(PASTA / "blaze_face_short_range.tflite")
                        ),
                        running_mode=vision.RunningMode.IMAGE,
                        min_detection_confidence=0.65,
                    )
                )
            except Exception:
                log.exception("Falha ao criar o detector de rosto")

        if "face_landmarker.task" not in self.modelos_faltando:
            try:
                self.detector_expressoes = vision.FaceLandmarker.create_from_options(
                    vision.FaceLandmarkerOptions(
                        base_options=BaseOptions(
                            model_asset_path=str(PASTA / "face_landmarker.task")
                        ),
                        running_mode=vision.RunningMode.IMAGE,
                        num_faces=1,
                        output_face_blendshapes=True,
                    )
                )
            except Exception:
                log.exception("Falha ao criar o detector de expressões")

        if "hand_landmarker.task" not in self.modelos_faltando:
            try:
                self.detector_maos = vision.HandLandmarker.create_from_options(
                    vision.HandLandmarkerOptions(
                        base_options=BaseOptions(
                            model_asset_path=str(PASTA / "hand_landmarker.task")
                        ),
                        running_mode=vision.RunningMode.IMAGE,
                        num_hands=2,
                        min_hand_detection_confidence=0.65,
                        min_hand_presence_confidence=0.65,
                        min_tracking_confidence=0.6,
                    )
                )
            except Exception:
                log.exception("Falha ao criar o detector de mãos")

    def montar_interface(self):
        central = QWidget()
        self.setCentralWidget(central)

        layout_principal = QVBoxLayout(central)
        layout_principal.setContentsMargins(12, 10, 12, 12)
        layout_principal.setSpacing(12)

        topo = QFrame()
        topo.setObjectName("topo")

        topo_layout = QHBoxLayout(topo)
        topo_layout.setContentsMargins(18, 10, 18, 10)

        marca = QLabel("J.A.R.V.I.S")
        marca.setObjectName("marca")

        self.status_online = QLabel("● ONLINE")
        self.status_online.setObjectName("online")

        self.relogio = QLabel()
        self.relogio.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.relogio.setStyleSheet("""
            color: #C7F8FF;
            background-color: #071923;
            border: 1px solid #173D52;
            border-radius: 10px;
            padding: 8px 18px;
            font-weight: bold;
        """)

        botao_config = QPushButton("⚙ Configurações")
        botao_config.clicked.connect(self.abrir_configuracoes)

        topo_layout.addWidget(marca)
        topo_layout.addSpacing(14)
        topo_layout.addWidget(self.status_online)
        topo_layout.addStretch()
        topo_layout.addWidget(self.relogio)
        topo_layout.addStretch()
        topo_layout.addWidget(botao_config)

        layout_principal.addWidget(topo)

        corpo = QHBoxLayout()
        corpo.setSpacing(14)

        coluna_esquerda = QVBoxLayout()
        coluna_esquerda.setSpacing(14)

        cartao_sistema, layout_sistema = criar_cartao("◉  STATUS DO SISTEMA")

        self.cpu = QLabel()
        self.cpu.setObjectName("valorGrande")

        self.ram = QLabel()
        self.ram.setObjectName("valorGrande")

        self.disco = QLabel()
        self.disco.setObjectName("textoPequeno")

        layout_sistema.addWidget(self.cpu)
        layout_sistema.addWidget(self.ram)
        layout_sistema.addWidget(self.disco)

        cartao_camera, layout_camera = criar_cartao("▣  CÂMERA")

        self.camera_preview = QLabel("Câmera inicializando...")
        self.camera_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_preview.setMinimumSize(280, 190)
        self.camera_preview.setMaximumHeight(210)
        self.camera_preview.setFixedHeight(200)
        self.camera_preview.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed
        )
        self.camera_preview.setStyleSheet("""
            color: #83AAB8;
            background-color: #06151E;
            border-radius: 10px;
            padding: 8px;
        """)

        self.camera_status = QLabel("● Câmera ativa | sem gravação")
        self.camera_status.setObjectName("textoPequeno")
        self.camera_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_status.setWordWrap(True)
        self.camera_status.setFixedHeight(36)
        self.camera_status.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed
        )

        self.botao_expandir_camera = QPushButton("Expandir câmera")
        self.botao_expandir_camera.clicked.connect(self.alternar_camera_expandida)

        layout_camera.addWidget(self.camera_preview)
        layout_camera.addWidget(self.camera_status)
        layout_camera.addWidget(self.botao_expandir_camera)

        cartao_tempo, layout_tempo = criar_cartao("◷  SESSÃO")

        self.tempo_ligada = QLabel()
        self.tempo_ligada.setObjectName("valorGrande")

        self.comandos = QLabel()
        self.comandos.setObjectName("textoPequeno")

        layout_tempo.addWidget(self.tempo_ligada)
        layout_tempo.addWidget(self.comandos)

        coluna_esquerda.addWidget(cartao_sistema)
        coluna_esquerda.addWidget(cartao_camera)
        coluna_esquerda.addWidget(cartao_tempo)
        coluna_esquerda.addStretch()

        centro = QVBoxLayout()
        centro.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.nucleo = NucleoJarvis()

        titulo_jarvis = QLabel("J.A.R.V.I.S")
        titulo_jarvis.setAlignment(Qt.AlignmentFlag.AlignCenter)
        titulo_jarvis.setStyleSheet("""
            color: #C8F8FF;
            font-size: 28px;
            font-weight: bold;
            letter-spacing: 8px;
        """)

        self.estado_voz = QLabel("● Aguardando você dizer: Jarvis")
        self.estado_voz.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.estado_voz.setStyleSheet("""
            color: #65EEFF;
            background-color: #082331;
            border-radius: 9px;
            padding: 10px 18px;
            font-weight: bold;
        """)

        centro.addStretch()
        centro.addWidget(self.nucleo, alignment=Qt.AlignmentFlag.AlignCenter)
        centro.addWidget(titulo_jarvis)
        centro.addSpacing(10)
        centro.addWidget(self.estado_voz)
        centro.addStretch()

        coluna_direita = QVBoxLayout()

        cartao_navegador, layout_navegador = criar_cartao("▣  NAVEGADOR")

        barra_navegador = QHBoxLayout()
        barra_navegador.setSpacing(4)
        self.endereco_navegador = QLineEdit()
        self.endereco_navegador.setPlaceholderText("Digite um endereço ou pesquise...")
        self.endereco_navegador.returnPressed.connect(self.navegar_pela_barra)

        botao_ir = QPushButton("↵")
        botao_ir.setObjectName("botaoNavegador")
        botao_ir.setToolTip("Abrir endereço")
        botao_ir.clicked.connect(self.navegar_pela_barra)
        barra_navegador.addWidget(self.endereco_navegador)
        barra_navegador.addWidget(botao_ir)

        botoes_navegador = QHBoxLayout()
        botoes_navegador.setSpacing(5)
        botao_voltar = QPushButton("Voltar")
        botao_voltar.setObjectName("botaoNavegador")
        botao_voltar.setText("‹")
        botao_voltar.setToolTip("Voltar")
        botao_voltar.clicked.connect(self.navegador_back)
        botao_avancar = QPushButton("Avançar")
        botao_avancar.setObjectName("botaoNavegador")
        botao_avancar.setText("›")
        botao_avancar.setToolTip("Avançar")
        botao_avancar.clicked.connect(self.navegador_forward)
        botao_recarregar = QPushButton("Recarregar")
        botao_recarregar.setObjectName("botaoNavegador")
        botao_recarregar.setText("↻")
        botao_recarregar.setToolTip("Recarregar página")
        botao_recarregar.clicked.connect(self.navegador_reload)
        botao_nova_aba = QPushButton("+")
        botao_nova_aba.setObjectName("botaoNavegador")
        botao_nova_aba.setToolTip("Nova aba")
        botao_nova_aba.clicked.connect(self.nova_aba_navegador)
        botao_expandir_navegador = QPushButton("⛶")
        botao_expandir_navegador.setObjectName("botaoNavegador")
        botao_expandir_navegador.setToolTip("Abrir navegador em janela separada")
        botao_expandir_navegador.clicked.connect(self.expandir_navegador)
        botoes_navegador.addWidget(botao_voltar)
        botoes_navegador.addWidget(botao_avancar)
        botoes_navegador.addWidget(botao_recarregar)
        botoes_navegador.addStretch()
        botoes_navegador.addWidget(botao_nova_aba)
        botoes_navegador.addWidget(botao_expandir_navegador)

        self.navegador = self.criar_navegador(QUrl("https://www.google.com"))
        self.navegador.setMinimumSize(300, 280)
        self.navegador.urlChanged.connect(
            lambda url: self.endereco_navegador.setText(url.toString())
        )
        self.navegador.loadFinished.connect(
            lambda carregou, view=self.navegador: self.processar_pagina_aba(view, carregou)
        )

        self.abas_navegador = QTabWidget()
        self.abas_navegador.setMinimumHeight(300)
        self.abas_navegador.setTabsClosable(True)
        self.abas_navegador.tabCloseRequested.connect(self.fechar_aba_navegador)
        self.abas_navegador.currentChanged.connect(self.trocar_aba_navegador)
        self.abas_navegador.addTab(self.navegador, "YouTube Music")
        layout_navegador.addLayout(barra_navegador)
        layout_navegador.addWidget(self.abas_navegador)
        layout_navegador.addLayout(botoes_navegador)
        coluna_direita.addWidget(cartao_navegador, 3)

        cartao_conversa, layout_conversa = criar_cartao("▤  CONVERSAS")

        self.historico = QTextBrowser()
        self.historico.setReadOnly(True)

        compositor = QHBoxLayout()

        self.entrada_chat = QLineEdit()
        self.entrada_chat.setPlaceholderText("Escreva uma mensagem para a Jarvis...")
        self.entrada_chat.returnPressed.connect(self.enviar_mensagem)

        botao_enviar = QPushButton("Enviar")
        botao_enviar.setObjectName("botaoAcao")
        botao_enviar.clicked.connect(self.enviar_mensagem)

        compositor.addWidget(self.entrada_chat)
        compositor.addWidget(botao_enviar)

        botoes_midia = QHBoxLayout()

        botao_imagem = QPushButton("Gerar imagem")
        botao_imagem.clicked.connect(self.pedir_imagem)

        botoes_midia.addWidget(botao_imagem)
        botoes_midia.addStretch()

        botoes_conversa = QHBoxLayout()

        botao_atualizar = QPushButton("Atualizar")
        botao_atualizar.setObjectName("botaoAcao")
        botao_atualizar.setText("↻")
        botao_atualizar.setToolTip("Atualizar conversa")
        botao_atualizar.clicked.connect(self.atualizar_historico)

        botao_limpar = QPushButton("Limpar tela")
        botao_limpar.setObjectName("botaoAcao")
        botao_limpar.setText("⌫")
        botao_limpar.setToolTip("Limpar conversa exibida")
        botao_limpar.clicked.connect(self.historico.clear)

        botao_esconder = QPushButton("Esconder painel")
        botao_esconder.setObjectName("botaoAcao")
        botao_esconder.setText("−")
        botao_esconder.setToolTip("Esconder painel")
        botao_esconder.clicked.connect(self.esconder_painel)

        botoes_conversa.addWidget(botao_atualizar)
        botoes_conversa.addWidget(botao_limpar)
        botoes_conversa.addWidget(botao_esconder)

        botao_expandir_chat = QPushButton("Expandir chat")
        botao_expandir_chat.setObjectName("botaoDestaque")
        botao_expandir_chat.setText("↗  Chat")
        botao_expandir_chat.setToolTip("Abrir chat em janela separada")
        botao_expandir_chat.clicked.connect(self.expandir_chat)
        botoes_conversa.addWidget(botao_expandir_chat)

        self.layout_conversa = layout_conversa
        self.historico.setMinimumHeight(170)
        layout_conversa.addWidget(self.historico)
        layout_conversa.addLayout(compositor)
        layout_conversa.addLayout(botoes_midia)
        layout_conversa.addLayout(botoes_conversa)

        coluna_direita.addWidget(cartao_conversa, 2)

        corpo.addLayout(coluna_esquerda, 3)
        corpo.addLayout(centro, 4)
        corpo.addLayout(coluna_direita, 6)

        layout_principal.addLayout(corpo)

    def navegar_pela_barra(self):
        texto = self.endereco_navegador.text().strip()
        if not texto:
            return

        if "://" not in texto:
            if " " in texto:
                texto = "https://www.google.com/search?q=" + quote_plus(texto)
            else:
                texto = "https://" + texto

        self.navegador.setUrl(QUrl(texto))

    def navegador_back(self):
        self.navegador.back()

    def navegador_forward(self):
        self.navegador.forward()

    def navegador_reload(self):
        self.navegador.reload()

    def criar_navegador(self, url, parent=None):
        navegador = QWebEngineView(parent)
        navegador.setPage(QWebEnginePage(self.perfil_navegador, navegador))
        navegador.setUrl(url)
        return navegador

    def nova_aba_navegador(self):
        navegador = self.criar_navegador(QUrl("https://www.google.com"))
        navegador.urlChanged.connect(
            lambda url, view=navegador: self.atualizar_endereco_aba(view, url)
        )
        navegador.loadFinished.connect(
            lambda carregou, view=navegador: self.processar_pagina_aba(view, carregou)
        )
        indice = self.abas_navegador.addTab(navegador, "Nova aba")
        self.abas_navegador.setCurrentIndex(indice)

    def fechar_aba_navegador(self, indice):
        if self.abas_navegador.count() <= 1:
            return

        widget = self.abas_navegador.widget(indice)
        self.abas_navegador.removeTab(indice)
        widget.deleteLater()

    def trocar_aba_navegador(self, indice):
        if indice < 0:
            return

        self.navegador = self.abas_navegador.widget(indice)
        self.endereco_navegador.setText(self.navegador.url().toString())

    def atualizar_endereco_aba(self, view, url):
        if view is self.navegador:
            self.endereco_navegador.setText(url.toString())

    def processar_pagina_aba(self, view, carregou):
        if not carregou:
            return

        url = view.url().toString()
        if "music.youtube.com/search" in url:
            QTimer.singleShot(
                3500,
                lambda: self.abrir_primeiro_resultado_musica(view)
            )
        elif "music.youtube.com/watch" in url:
            QTimer.singleShot(
                3500,
                lambda: self.tocar_musica_carregada(view)
            )

    def expandir_navegador(self):
        if self.janela_navegador_expandida is not None:
            self.janela_navegador_expandida.raise_()
            self.janela_navegador_expandida.activateWindow()
            return

        self.janela_navegador_expandida = QDialog(self)
        self.janela_navegador_expandida.setWindowTitle("J.A.R.V.I.S | Navegador")
        self.janela_navegador_expandida.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self.janela_navegador_expandida.setStyleSheet(
            "QDialog { background-color: #02070B; }"
        )
        layout = QVBoxLayout(self.janela_navegador_expandida)
        layout.setContentsMargins(8, 8, 8, 8)
        navegador = self.criar_navegador(
            self.navegador.url(),
            self.janela_navegador_expandida
        )
        navegador.loadFinished.connect(
            lambda carregou, view=navegador: self.processar_pagina_aba(view, carregou)
        )
        layout.addWidget(navegador)
        self.janela_navegador_expandida.finished.connect(
            self.fechar_navegador_expandido
        )
        self.janela_navegador_expandida.resize(720, 480)
        self.janela_navegador_expandida.show()

    def fechar_navegador_expandido(self):
        self.janela_navegador_expandida = None

    def expandir_chat(self):
        if self.janela_chat_expandida is not None:
            self.janela_chat_expandida.raise_()
            self.janela_chat_expandida.activateWindow()
            return

        self.layout_conversa.removeWidget(self.historico)
        self.janela_chat_expandida = QDialog(self)
        self.janela_chat_expandida.setWindowTitle("J.A.R.V.I.S | Chat")
        self.janela_chat_expandida.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self.janela_chat_expandida.setStyleSheet(self.styleSheet())
        layout = QVBoxLayout(self.janela_chat_expandida)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self.historico)
        fechar = QPushButton("←")
        fechar.setObjectName("botaoNavegador")
        fechar.setToolTip("Voltar ao painel")
        fechar.clicked.connect(self.janela_chat_expandida.close)
        layout.addWidget(fechar)
        self.janela_chat_expandida.finished.connect(self.restaurar_chat)
        self.janela_chat_expandida.resize(640, 520)
        self.janela_chat_expandida.show()

    def restaurar_chat(self):
        if self.janela_chat_expandida is None:
            return

        self.janela_chat_expandida = None
        self.layout_conversa.insertWidget(0, self.historico)
        self.atualizar_historico()

    def abrir_primeiro_resultado_musica(self, view=None):
        view = view or self.navegador
        if "music.youtube.com/search" not in view.url().toString():
            return

        script = """
            (() => {
                const seletores = [
                    "ytmusic-responsive-list-item-renderer a[href*='/watch']",
                    "ytmusic-two-column-search-results-renderer a[href*='/watch']",
                    "a[href*='/watch?v=']"
                ];
                let resultado = null;
                for (const seletor of seletores) {
                    resultado = document.querySelector(seletor);
                    if (resultado) break;
                }
                return resultado ? resultado.href : '';
            })();
        """
        view.page().runJavaScript(
            script,
            lambda url: view.setUrl(QUrl(url)) if url else None
        )

    def tocar_musica_carregada(self, view=None):
        view = view or self.navegador
        if "music.youtube.com/watch" not in view.url().toString():
            return

        script = """
            (() => {
                const seletores = [
                    "ytmusic-player-bar #play-pause-button",
                    "tp-yt-paper-icon-button[aria-label*='Play']",
                    "tp-yt-paper-icon-button[aria-label*='Reproduzir']",
                    "button[aria-label*='Play']",
                    "button[aria-label*='Reproduzir']"
                ];
                for (const seletor of seletores) {
                    const tocar = document.querySelector(seletor);
                    if (tocar) {
                        tocar.click();
                        break;
                    }
                }
            })();
        """
        view.page().runJavaScript(script)

    def atualizar_painel(self):
        agora = datetime.now()
        self.processar_eventos_da_interface()
        self.mostrar_avisos_pendentes()

        self.relogio.setText(
            agora.strftime("%H:%M:%S  |  %d/%m/%Y")
        )

        uso_cpu = psutil.cpu_percent()
        memoria = psutil.virtual_memory()
        disco = psutil.disk_usage(PASTA.anchor)

        self.cpu.setText(f"CPU  {uso_cpu:.0f}%")
        self.ram.setText(
            f"RAM  {memoria.percent:.0f}%  "
            f"({memoria.used / 1024**3:.1f} GB)"
        )

        self.disco.setText(
            f"Disco: {disco.used / 1024**3:.0f} GB usados de "
            f"{disco.total / 1024**3:.0f} GB"
        )

        duracao = datetime.now() - self.inicio
        duracao_texto = str(timedelta(seconds=int(duracao.total_seconds())))

        self.tempo_ligada.setText(duracao_texto)

        quantidade_comandos = len(
            [
                item for item in self.ler_memoria()
                if item.get("tipo") == "Usuário"
            ]
        )

        self.comandos.setText(
            f"Comandos registrados nesta memória: {quantidade_comandos}"
        )

        self.atualizar_historico()

    def mostrar_avisos_pendentes(self):
        """Mostra erros capturados pelo hook global, com limite de frequência."""

        while AVISOS_PENDENTES:
            mensagem = AVISOS_PENDENTES.popleft()
            agora = datetime.now().timestamp()

            if agora < getattr(self, "_proximo_aviso_de_erro", 0):
                log.warning("Aviso suprimido na tela: %s", mensagem)
                continue

            if not hasattr(self, "tray"):
                AVISOS_PENDENTES.appendleft(mensagem)
                break

            self._proximo_aviso_de_erro = agora + 15
            self.mostrar_aviso("Ocorreu um erro interno. Veja jarvis.tela.log", mensagem, "erro")

    def atualizar_camera(self):
        """Ponto de entrada do timer da câmera: nunca pode derrubar o app.

        Antes, uma exceção aqui (por exemplo `detect()` num detector já
        fechado) subia pela thread do Qt a cada 220 ms, para sempre — e sem
        `sys.excepthook` isso podia até encerrar o processo.
        """

        if self.recursos_liberados or self.encerrando:
            return

        try:
            self._atualizar_camera()
        except Exception:
            agora = datetime.now().timestamp()

            if agora >= self.erro_na_camera_ate:
                self.erro_na_camera_ate = agora + 30
                log.exception("Falha ao atualizar a câmera")

            try:
                self.camera_status.setText("● Câmera com erro — veja jarvis.tela.log")
            except Exception:
                pass

    def _atualizar_camera(self):
        config = carregar_config()

        if self.detector_rosto is None and config.get("camera_ativada", True):
            # Modelos ausentes: explica na tela em vez de estourar exceção.
            self.camera_preview.setText("Modelos da câmera não instalados")
            self.camera_status.setText(
                "● Execute 'python preparar_modelos.py' para habilitar a câmera"
            )
            return

        if not config.get("camera_ativada", True):
            if self.camera is not None:
                self.camera.release()
                self.camera = None
            self.camera_preview.setText("Câmera desligada")
            self.camera_status.setText("● Câmera desligada")
            return

        if self.camera is None:
            self.camera = cv2.VideoCapture(0, cv2.CAP_DSHOW)
            self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)

        if not self.camera.isOpened():
            self.camera.release()
            self.camera = None
            self.camera_preview.setText("Não foi possível abrir a câmera")
            self.camera_status.setText("● Câmera indisponível")
            return

        sucesso, frame = self.camera.read()

        if not sucesso:
            self.camera_status.setText("● Não foi possível ler a câmera")
            return

        self.ultimo_frame_ok = datetime.now().timestamp()
        self.contador_de_frames += 1
        frame = cv2.flip(frame, 1)
        imagem_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        resultado_rosto = self.detector_rosto.detect(
            MpImage(
                image_format=MpImageFormat.SRGB,
                data=imagem_rgb
            )
        )
        tem_rosto = bool(resultado_rosto.detections)
        emocao = "sem rosto"
        gesto = "nenhum"
        agora = datetime.now().timestamp()

        if not tem_rosto:
            self._ultima_expressao = None

        if tem_rosto:
            self.ultimo_rosto = datetime.now().timestamp()
            self.rosto_confirmado = True
            self.desenhar_rosto(frame, resultado_rosto.detections)

            if self.detector_expressoes is not None and (
                config.get("detectar_emocoes", True) or config.get("exoesqueleto_facial", True)
            ):
                if self.contador_de_frames % self.cadencia_de_expressoes == 0:
                    try:
                        self._ultima_expressao = self.detector_expressoes.detect(
                            MpImage(
                                image_format=MpImageFormat.SRGB,
                                data=imagem_rgb
                            )
                        )
                    except Exception:
                        log.exception("Falha ao analisar as expressões faciais")
                        self._ultima_expressao = None

                resultado_expressao = self._ultima_expressao

                if resultado_expressao is not None:
                    if config.get("exoesqueleto_facial", True):
                        for landmarks in resultado_expressao.face_landmarks:
                            self.desenhar_exoesqueleto_facial(frame, landmarks)

                    if config.get("detectar_emocoes", True):
                        emocao = self.estimar_emocao(resultado_expressao)
                        self.emocao_atual = emocao



        if config.get("reagir_a_gestos", True) and self.detector_maos is not None:
            resultado_maos = self.detector_maos.detect(
                MpImage(
                    image_format=MpImageFormat.SRGB,
                    data=imagem_rgb
                )
            )
            maos = resultado_maos.hand_landmarks
            holograma_controlado = bool(maos)
            gestos_detectados = []

            for mao in maos:
                self.desenhar_mao(frame, mao)
                gesto_detectado = self.detectar_gesto(mao)
                if gesto_detectado != "nenhum":
                    gestos_detectados.append(gesto_detectado)

            if gestos_detectados:
                gesto = gestos_detectados[0]

            self.atualizar_controle_holograma_duas_maos(maos)

            if gesto != "nenhum":
                self.gesto_atual = gesto
                self.ultimo_gesto = gesto
                self.ultimo_gesto_tempo = agora

            self.processar_comandos_mao(frame, maos)

            if not maos:
                self.mao_segura_holograma = False
                self.centro_mao_anterior = None
                self.distancia_mao_anterior = None

            if not holograma_controlado:
                self.mao_segura_holograma = False
                self.centro_mao_anterior = None
                self.distancia_mao_anterior = None

        maos = locals().get("maos", []) or []

        if self.holograma_visivel and config.get("holograma_ativado", True):
            self.desenhar_holograma_67(frame)

        tempo_sem_rosto = (
            None if self.ultimo_rosto is None
            else agora - self.ultimo_rosto
        )

        self.atualizar_bloqueio_por_ausencia(config, agora, tempo_sem_rosto)

        imagem = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        altura, largura, canais = imagem.shape
        qimagem = QImage(
            imagem.data,
            largura,
            altura,
            canais * largura,
            QImage.Format.Format_RGB888
        ).copy()
        pixmap = QPixmap.fromImage(qimagem)
        self.camera_preview.setPixmap(
            pixmap.scaled(
                self.camera_preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
        )

        if self.camera_preview_cheia is not None:
            self.camera_preview_cheia.setPixmap(
                pixmap.scaled(
                    self.camera_preview_cheia.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
            )
        quantidade_maos = len(maos) if config.get("reagir_a_gestos", True) else 0
        estado = "rosto presente" if tem_rosto else (
            "sem rosto, aguardando" if self.rosto_confirmado else
            "procurando seu rosto"
        )
        reacao = self.reacao_gesto(self.ultimo_gesto)
        if agora - self.ultimo_gesto_tempo > 2.5:
            reacao = ""
            self.gesto_atual = "nenhum"

        self.camera_status.setText(
            f"● Câmera ativa | {estado} | expressão: {emocao} | "
            f"mãos: {quantidade_maos} | gesto: {reacao or 'nenhum'} | sem gravação"
        )

    def atualizar_bloqueio_por_ausencia(self, config, agora, tempo_sem_rosto):
        """Bloqueia o PC quando você sai — com trava de segurança.

        Regras novas (o antigo bloqueava direto, e podia travar o computador
        enquanto você trabalhava, se a câmera falhasse ou você ficasse de lado):

        * a câmera precisa estar saudável (frame lido nos últimos 3 s);
        * a sessão precisa ter pelo menos 1 minuto;
        * o bloqueio é **avisado** 10 segundos antes, e cancelado se o rosto
          aparecer nesse intervalo.
        """

        if not config.get("bloquear_ao_sair", False):
            return

        if os.name != "nt":
            return

        if not self.rosto_confirmado or tempo_sem_rosto is None:
            return

        # Câmera saudável?
        if agora - self.ultimo_frame_ok > 3:
            return

        if (datetime.now() - self.inicio).total_seconds() < 60:
            return

        if tempo_sem_rosto < 8:
            self.bloqueio_agendado_para = 0.0
            self.aviso_de_bloqueio_dado = False
            return

        if self.bloqueio_agendado_para == 0.0:
            self.bloqueio_agendado_para = agora + 10

            if not self.aviso_de_bloqueio_dado:
                self.aviso_de_bloqueio_dado = True
                log.info("Sem rosto há %.1fs: bloqueio agendado em 10s", tempo_sem_rosto)
                self.camera_status.setText(
                    "● Sem rosto: vou bloquear o computador em 10 segundos (mexa-se para cancelar)"
                )
                if hasattr(self, "tray"):
                    self.tray.showMessage(
                        "Jarvis",
                        "Não estou vendo você. Vou bloquear o computador em 10 segundos.",
                        QSystemTrayIcon.MessageIcon.Information,
                        6000,
                    )

        if agora >= self.bloqueio_agendado_para:
            self.bloqueio_agendado_para = 0.0
            self.aviso_de_bloqueio_dado = False
            self.ultimo_bloqueio = agora
            log.warning("Bloqueando o computador por ausência")
            subprocess.Popen(
                ["rundll32.exe", "user32.dll,LockWorkStation"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

    def processar_eventos_da_interface(self):
        """Atende os recados vindos do processo de voz.

        Antes isso se chamava `processar_eventos_holograma` e o único evento
        tratado (`mostrar_holograma`) nunca era emitido por ninguém — ou seja,
        o holograma era impossível de ligar. Agora o painel também mostra
        avisos de erro e o estado do microfone/reconhecimento.
        """

        for evento in consumir_eventos():
            if not isinstance(evento, dict):
                continue

            tipo = evento.get("tipo")
            dados = evento.get("dados") or {}

            if tipo == "mostrar_holograma":
                self.holograma_visivel = bool(dados.get("visivel", True))
                self.camera_status.setText(
                    "● Holograma "
                    + ("ligado" if self.holograma_visivel else "desligado")
                )
                log.info("Holograma %s", "ligado" if self.holograma_visivel else "desligado")

            elif tipo == "esconder_painel":
                log.info("Pedido de voz: esconder o painel")
                self.esconder_painel()

            elif tipo == "definir_camera":
                ativada = bool(dados.get("ativada", True))
                config = carregar_config()
                config["camera_ativada"] = ativada
                salvar_config(config)
                log.info("Câmera %s por pedido de voz", "ligada" if ativada else "desligada")
                self.camera_status.setText(
                    "● Câmera " + ("ligada" if ativada else "desligada")
                )

            elif tipo == "navegar_navegador":
                url = dados.get("url", "")
                if url:
                    self.navegador.setUrl(QUrl(url))
                    log.info("Painel navegando para %s", url)

            elif tipo == "estado_voz":
                self.atualizar_estado_da_voz(dados)

            elif tipo == "aviso":
                self.mostrar_aviso(
                    str(dados.get("texto", "")),
                    str(dados.get("detalhe", "")),
                    str(dados.get("nivel", "info")),
                )

    def atualizar_estado_da_voz(self, dados):
        """Mostra na tela o que o reconhecimento está fazendo."""

        self.ultimo_estado_voz = dict(dados)

        if dados.get("processando"):
            self.estado_voz.setText("● Pensando...")
            return

        if not dados:
            return

        motor = dados.get("motor") or "—"
        offline = " | modo offline" if dados.get("modo_offline") else ""

        if dados.get("erro"):
            self.estado_voz.setText(f"● Microfone com erro{motor and ' | motor ' + motor}")
            return

        motivo = dados.get("motivo", "")
        relacao = dados.get("relacao")
        detalhe = f" | sinal {relacao}x do limiar" if isinstance(relacao, (int, float)) else ""

        self.estado_voz.setText(
            f"● Ouvindo (motor: {motor}{offline}){detalhe}"
            + (f" — {motivo}" if motivo and motivo != "aguardando voz" else "")
        )

    def mostrar_aviso(self, texto, detalhe="", nivel="info"):
        if not texto:
            return

        log.warning("Aviso na tela (%s): %s %s", nivel, texto, detalhe)

        if hasattr(self, "estado_voz"):
            self.estado_voz.setText(f"● {texto}")

        if hasattr(self, "tray"):
            self.tray.showMessage(
                "Jarvis",
                texto + (f"\n{detalhe}" if detalhe else ""),
                QSystemTrayIcon.MessageIcon.Warning
                if nivel == "erro"
                else QSystemTrayIcon.MessageIcon.Information,
                6000,
            )

    def atualizar_controle_holograma_duas_maos(self, maos):
        pincas = []

        for mao in maos:
            distancia = max(
                ((mao[4].x - mao[8].x) ** 2 + (mao[4].y - mao[8].y) ** 2) ** 0.5,
                0.001
            )
            if distancia < 0.16:
                pincas.append((mao[8].x, mao[8].y))

        if len(pincas) < 2:
            self.mao_segura_holograma = False
            self.centro_mao_anterior = None
            self.distancia_mao_anterior = None
            return

        centro = (
            (pincas[0][0] + pincas[1][0]) / 2,
            (pincas[0][1] + pincas[1][1]) / 2
        )
        distancia = max(
            ((pincas[0][0] - pincas[1][0]) ** 2 +
             (pincas[0][1] - pincas[1][1]) ** 2) ** 0.5,
            0.001
        )

        if self.centro_mao_anterior is not None:
            delta_x = centro[0] - self.centro_mao_anterior[0]
            delta_y = centro[1] - self.centro_mao_anterior[1]
            self.holograma_rotacao_y = (self.holograma_rotacao_y + delta_x * 720) % 360
            self.holograma_rotacao_x = (self.holograma_rotacao_x + delta_y * 360) % 360

        if self.distancia_mao_anterior is not None:
            self.holograma_escala = max(
                0.55,
                min(2.2, self.holograma_escala * distancia / self.distancia_mao_anterior)
            )

        self.mao_segura_holograma = True
        self.centro_mao_anterior = centro
        self.distancia_mao_anterior = distancia

    def alternar_camera_expandida(self):
        if self.janela_camera_cheia is None:
            self.janela_camera_cheia = QDialog(self)
            self.janela_camera_cheia.setWindowTitle("J.A.R.V.I.S | Câmera")
            self.janela_camera_cheia.setStyleSheet(
                "QDialog { background-color: #02070B; }"
            )

            layout = QVBoxLayout(self.janela_camera_cheia)
            layout.setContentsMargins(0, 0, 0, 0)

            self.camera_preview_cheia = QLabel("Câmera inicializando...")
            self.camera_preview_cheia.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.camera_preview_cheia.setStyleSheet(
                "color: #83AAB8; background-color: #02070B;"
            )
            layout.addWidget(self.camera_preview_cheia)

            sair = QPushButton("Sair da tela cheia")
            sair.clicked.connect(self.fechar_camera_cheia)
            layout.addWidget(sair)

            self.janela_camera_cheia.finished.connect(self.camera_cheia_fechada)

        self.camera_expandida = True
        self.botao_expandir_camera.setText("Sair da tela cheia")
        self.janela_camera_cheia.showFullScreen()

    def camera_cheia_fechada(self):
        self.camera_expandida = False
        self.ultimo_estado_voz = {}
        self.botao_expandir_camera.setText("Expandir câmera")

    def fechar_camera_cheia(self):
        if self.janela_camera_cheia is not None:
            self.janela_camera_cheia.close()

    def desenhar_holograma_67(self, frame):
        altura, largura = frame.shape[:2]
        centro_x = largura // 2
        centro_y = altura // 2
        escala = self.holograma_escala
        rotacao = [
            math.radians(self.holograma_rotacao_x),
            math.radians(self.holograma_rotacao_y),
            0
        ]
        vertices, arestas = self.malha_3d_67()
        projetados = [
            self.projetar_ponto_3d(ponto, rotacao, escala, centro_x, centro_y)
            for ponto in vertices
        ]

        for inicio, fim in arestas:
            cv2.line(
                frame,
                projetados[inicio],
                projetados[fim],
                (70, 220, 255),
                2,
                cv2.LINE_AA
            )

        cv2.putText(
            frame,
            "67 3D",
            (centro_x - int(65 * escala), centro_y + int(145 * escala)),
            cv2.FONT_HERSHEY_DUPLEX,
            0.7 * escala,
            (120, 240, 255),
            1,
            cv2.LINE_AA
        )

    def malha_3d_67(self):
        frente = [
            (-0.9, -1.2, 0.25), (-0.2, -1.2, 0.25), (-0.8, -0.2, 0.25),
            (-0.1, -0.2, 0.25), (-0.8, 0.8, 0.25), (-0.1, 0.8, 0.25),
            (0.25, -1.2, 0.25), (1.0, -1.2, 0.25), (0.35, 0.8, 0.25),
            (0.85, 0.8, 0.25), (0.65, 0.1, 0.25), (0.45, -0.5, 0.25)
        ]
        vertices = frente + [(x, y, -0.25) for x, y, _ in frente]
        arestas = []

        for deslocamento in [0, len(frente)]:
            arestas.extend(
                (deslocamento + inicio, deslocamento + fim)
                for inicio, fim in [
                    (0, 1), (0, 2), (2, 3), (2, 4), (4, 5),
                    (6, 7), (7, 8), (8, 9), (9, 10), (10, 11)
                ]
            )

        arestas.extend((indice, indice + len(frente)) for indice in range(len(frente)))
        return vertices, arestas

    def projetar_ponto_3d(self, ponto, rotacao, escala, centro_x, centro_y):
        x, y, z = ponto
        rx, ry, _ = rotacao
        cos_x, sin_x = math.cos(rx), math.sin(rx)
        cos_y, sin_y = math.cos(ry), math.sin(ry)
        y, z = y * cos_x - z * sin_x, y * sin_x + z * cos_x
        x, z = x * cos_y + z * sin_y, -x * sin_y + z * cos_y
        perspectiva = 1 / max(0.35, 1 - z * 0.12)
        return (
            int(centro_x + x * 115 * escala * perspectiva),
            int(centro_y + y * 115 * escala * perspectiva)
        )

    def desenhar_rosto(self, frame, deteccoes):
        altura, largura = frame.shape[:2]

        for deteccao in deteccoes:
            caixa = deteccao.bounding_box
            x = int(caixa.origin_x)
            y = int(caixa.origin_y)
            direita = min(largura, x + int(caixa.width))
            baixo = min(altura, y + int(caixa.height))
            cv2.rectangle(
                frame,
                (max(0, x), max(0, y)),
                (direita, baixo),
                (80, 240, 160),
                2
            )

    def desenhar_exoesqueleto_facial(self, frame, landmarks):
        altura, largura = frame.shape[:2]
        pontos = [
            (
                max(0, min(largura - 1, int(ponto.x * largura))),
                max(0, min(altura - 1, int(ponto.y * altura)))
            )
            for ponto in landmarks
        ]

        for conexao in vision.FaceLandmarksConnections.FACE_LANDMARKS_CONTOURS:
            if conexao.start >= len(pontos) or conexao.end >= len(pontos):
                continue

            cv2.line(
                frame,
                pontos[conexao.start],
                pontos[conexao.end],
                (255, 170, 40),
                1,
                cv2.LINE_AA
            )

        for indice in range(0, len(pontos), 6):
            cv2.circle(frame, pontos[indice], 1, (120, 240, 255), -1)

    def processar_comandos_mao(self, frame, maos):
        if not maos:
            self.ultimo_comando_mao = "nenhum"
            self.centro_mao_comando = None
            self.movimento_acumulado_x = 0.0
            self.movimento_acumulado_y = 0.0
            return

        mao = maos[0]
        agora = datetime.now().timestamp()
        centro = (
            sum(ponto.x for ponto in mao) / len(mao),
            sum(ponto.y for ponto in mao) / len(mao)
        )
        movimento_x = 0 if self.centro_mao_comando is None else centro[0] - self.centro_mao_comando[0]
        movimento_y = 0 if self.centro_mao_comando is None else centro[1] - self.centro_mao_comando[1]
        self.centro_mao_comando = centro

        indicador = mao[8].y < mao[6].y
        medio = mao[12].y < mao[10].y
        anelar = mao[16].y < mao[14].y
        mindinho = mao[20].y < mao[18].y
        dedos_estendidos = sum([indicador, medio, anelar, mindinho])
        palma_aberta = dedos_estendidos >= 4
        paz = indicador and medio and not anelar and not mindinho

        comando = "nenhum"
        if palma_aberta:
            self.movimento_acumulado_x += movimento_x
            self.movimento_acumulado_y += movimento_y
        else:
            self.movimento_acumulado_x = 0.0
            self.movimento_acumulado_y = 0.0

        if palma_aberta and abs(self.movimento_acumulado_y) >= 0.08 and abs(self.movimento_acumulado_y) >= abs(self.movimento_acumulado_x):
            comando = "rolar_cima" if self.movimento_acumulado_y < 0 else "rolar_baixo"
            if agora - self.ultimo_tempo_comando_mao >= 0.18:
                quantidade = 4 if self.movimento_acumulado_y < 0 else -4
                self.scroll_page(quantidade)
                self.movimento_acumulado_y = 0.0
                self.ultimo_tempo_comando_mao = agora
        elif palma_aberta and abs(self.movimento_acumulado_x) >= 0.18 and abs(self.movimento_acumulado_x) > abs(self.movimento_acumulado_y):
            comando = "avancar" if self.movimento_acumulado_x < 0 else "voltar"
            if comando != self.ultimo_comando_mao and agora - self.ultimo_tempo_comando_mao >= 0.8:
                self.navegar_historico("avancar" if self.movimento_acumulado_x < 0 else "voltar")
                self.movimento_acumulado_x = 0.0
                self.ultimo_tempo_comando_mao = agora
        elif paz:
            comando = "paz"
            if self.ultimo_comando_mao != "paz" and agora - self.ultimo_tempo_comando_mao >= 0.8:
                self.alternar_reproducao()
                self.ultimo_tempo_comando_mao = agora

        self.ultimo_comando_mao = comando
        if comando != "nenhum":
            cv2.putText(frame, f"Gesto: {comando}", (18, 34), cv2.FONT_HERSHEY_DUPLEX, 0.7, (120, 240, 255), 1, cv2.LINE_AA)

    def scroll_page(self, quantidade):
        self.enviar_tecla_windows(
            "{PGUP}" if quantidade > 0 else "{PGDN}",
            repeticoes=min(4, max(1, abs(int(quantidade)) // 2)),
        )

    def navegar_historico(self, direcao):
        self.enviar_tecla_windows("%{LEFT}" if direcao == "voltar" else "%{RIGHT}")

    def alternar_reproducao(self):
        self.enviar_tecla_windows("{SPACE}")

    def enviar_tecla_windows(self, tecla, repeticoes=1):
        """Envia a tecla direto pelo Windows, sem abrir um PowerShell por gesto.

        Antes, cada gesto de mão abria um processo `powershell.exe` para usar
        `WScript.Shell.SendKeys` — centenas de milissegundos de atraso, um
        processo novo a cada gesto, e o atalho ia para **qualquer** janela em
        foco. Agora é a mesma técnica das teclas de mídia do main.py
        (`keybd_event`), sem criar processo nenhum.
        """

        if os.name != "nt":
            log.warning("Teclas de gesto não existem neste sistema (%s)", tecla)
            return False

        alt = tecla.startswith("%")
        nome = tecla.replace("%", "").strip("{}").upper()
        codigo = CODIGOS_DE_TECLA.get(nome)

        if codigo is None:
            log.warning("Tecla de gesto desconhecida: %r", tecla)
            return False

        try:
            usuario32 = ctypes.WinDLL("user32", use_last_error=True)
        except Exception:
            log.exception("Não consegui acessar a API de teclado do Windows")
            return False

        try:
            for _ in range(max(1, int(repeticoes))):
                if alt:
                    usuario32.keybd_event(CODIGOS_DE_TECLA["ALT"], 0, 0, 0)

                usuario32.keybd_event(codigo, 0, 0, 0)
                usuario32.keybd_event(codigo, 0, 2, 0)

                if alt:
                    usuario32.keybd_event(CODIGOS_DE_TECLA["ALT"], 0, 2, 0)

                time.sleep(0.02)

            return True
        except Exception:
            log.exception("Falha ao enviar a tecla de gesto %r", tecla)
            return False

    def desenhar_mao(self, frame, mao):
        altura, largura = frame.shape[:2]
        pontos = [
            (int(ponto.x * largura), int(ponto.y * altura))
            for ponto in mao
        ]
        conexoes = [
            (0, 1), (1, 2), (2, 3), (3, 4),
            (0, 5), (5, 6), (6, 7), (7, 8),
            (0, 9), (9, 10), (10, 11), (11, 12),
            (0, 13), (13, 14), (14, 15), (15, 16),
            (0, 17), (17, 18), (18, 19), (19, 20),
            (5, 9), (9, 13), (13, 17)
        ]

        for inicio, fim in conexoes:
            cv2.line(frame, pontos[inicio], pontos[fim], (255, 190, 60), 2)

        for ponto in pontos:
            cv2.circle(frame, ponto, 3, (255, 255, 255), -1)

    def detectar_gesto(self, mao):
        indicador = mao[8].y < mao[6].y
        medio = mao[12].y < mao[10].y
        anelar = mao[16].y < mao[14].y
        minimo = mao[20].y < mao[18].y

        dedos_estendidos = sum([indicador, medio, anelar, minimo])

        if medio and not indicador and not anelar and not minimo:
            return "dedo_do_meio"

        if indicador and medio and not anelar and not minimo:
            return "paz"

        if dedos_estendidos == 4:
            return "mao_aberta"

        if dedos_estendidos == 0:
            return "punho"

        return "nenhum"

    def reacao_gesto(self, gesto):
        reacoes = {
            "dedo_do_meio": "🖕 Vai tomar no cu 😡",
            "paz": "✌️ Paz e respeito ✌️",
            "mao_aberta": "🖐️ Oi!",
            "punho": "✊ Força!"
        }
        return reacoes.get(gesto, "")

    def estimar_emocao(self, resultado):
        if not resultado.face_blendshapes:
            return "indisponível"

        sinais = {
            item.category_name: item.score
            for item in resultado.face_blendshapes[0]
        }

        sorriso = max(
            sinais.get("mouthSmileLeft", 0),
            sinais.get("mouthSmileRight", 0)
        )
        boca_aberta = sinais.get("jawOpen", 0)
        sobrancelha_alta = sinais.get("browInnerUp", 0)
        sobrancelha_baixa = max(
            sinais.get("browDownLeft", 0),
            sinais.get("browDownRight", 0)
        )
        canto_boca_baixo = max(
            sinais.get("mouthFrownLeft", 0),
            sinais.get("mouthFrownRight", 0)
        )

        if sorriso > 0.45:
            return "possível alegria"

        if boca_aberta > 0.5 and sobrancelha_alta > 0.3:
            return "possível surpresa"

        if sobrancelha_baixa > 0.45:
            return "possível tensão"

        if canto_boca_baixo > 0.4:
            return "possível tristeza"

        return "neutro"

    def ler_memoria(self):
        """Lê a memória reaproveitando o último resultado quando o arquivo não mudou.

        O painel atualiza uma vez por segundo; sem cache, isso relia e
        reprocessava o arquivo inteiro (que só cresce) duas vezes por segundo.
        """

        caminho = ARQUIVO_MEMORIA if ARQUIVO_MEMORIA.exists() else ARQUIVO_MEMORIA_LEGADO

        try:
            estado = caminho.stat()
            assinatura = (estado.st_mtime_ns, estado.st_size, caminho.name)
        except OSError:
            self._memoria_cache = []
            self._memoria_assinatura = None
            return []

        if getattr(self, "_memoria_assinatura", None) == assinatura:
            return self._memoria_cache

        dados = ler_jsonl(ARQUIVO_MEMORIA) if ARQUIVO_MEMORIA.exists() else ler_json(ARQUIVO_MEMORIA_LEGADO, [])
        self._memoria_cache = (
            [item for item in dados if isinstance(item, dict)]
            if isinstance(dados, list)
            else []
        )
        self._memoria_assinatura = assinatura
        return self._memoria_cache

    def atualizar_historico(self):
        barra = self.historico.verticalScrollBar()
        estava_no_final = barra.value() == barra.maximum()
        posicao_anterior = barra.value()

        memorias = self.ler_memoria()
        mensagens = []

        for item in memorias[-80:]:
            if not isinstance(item, dict):
                continue
            tipo = item.get("tipo", "Jarvis")
            texto = html.escape(item.get("texto", ""))
            data = html.escape(item.get("data", ""))
            classe = "usuario" if tipo == "Usuário" else "jarvis"
            nome = "Você" if tipo == "Usuário" else "Jarvis"
            mensagens.append(
                f"<div class='mensagem {classe}'>"
                f"<b>{nome}</b><small>{data}</small><br>{texto}</div>"
            )

        conteudo = "".join(mensagens) or "<p>Ainda não há mensagens.</p>"

        if conteudo == self.ultimo_html_chat:
            return

        self.ultimo_html_chat = conteudo
        self.historico.setHtml(conteudo)

        if estava_no_final:
            barra.setValue(barra.maximum())
        else:
            barra.setValue(posicao_anterior)

    def enviar_mensagem(self):
        texto = self.entrada_chat.text().strip()

        if not texto:
            return

        self.enfileirar_comando(texto)
        self.entrada_chat.clear()
        self.atualizar_historico()

    def pedir_imagem(self):
        texto, confirmou = QInputDialog.getText(
            self,
            "Gerar imagem",
            "Descreva a imagem ou o tema da busca:"
        )

        if confirmou and texto.strip():
            self.enfileirar_comando(f"gerar imagem {texto.strip()}")

    def enfileirar_comando(self, texto):
        """Entrega o comando para o processo de voz.

        Cada comando é um arquivo próprio (jarvis_comum), então não existe
        mais o risco de o Jarvis ler o arquivo no mesmo instante em que o
        painel grava — o que fazia o comando simplesmente sumir.
        """

        if not publicar_comando(texto):
            log.error("Não consegui entregar o comando ao processo de voz: %r", texto)
            return False

        log.info("Comando enviado pelo painel: %r", texto)
        return True

    def abrir_configuracoes(self):
        janela = QDialog(self)
        janela.setWindowTitle("Configurações da Jarvis")
        janela.setMinimumWidth(420)
        janela.setStyleSheet(self.styleSheet() + """
            QDialog, QDialog QWidget {
                background-color: #071923;
                color: #DDF9FF;
            }

            QDialog QLabel {
                color: #DDF9FF;
                font-size: 13px;
            }

            QDialog QCheckBox {
                color: #DDF9FF;
                spacing: 8px;
                padding: 8px 4px;
            }

            QDialog QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 1px solid #54CBE8;
                border-radius: 4px;
                background-color: #06151E;
            }

            QDialog QCheckBox::indicator:checked {
                background-color: #27A8C7;
                border: 1px solid #8EEBFF;
            }

            QDialog QPushButton {
                min-height: 34px;
            }
        """)

        layout = QVBoxLayout(janela)

        texto = QLabel(
            "As opções são salvas no seu computador e aplicadas na hora "
            "(câmera, gestos e holograma) ou na próxima resposta da Jarvis (voz)."
        )
        texto.setWordWrap(True)

        config = carregar_config()

        # -- microfone -------------------------------------------------------
        rotulo_aviso_microfone = QLabel("")
        rotulo_aviso_microfone.setWordWrap(True)
        rotulo_aviso_microfone.setObjectName("textoPequeno")
        rotulo_microfone = QLabel("Microfone:")
        combo_microfone = QComboBox()
        combo_microfone.addItem("Automático (recomendado)", "")

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        try:
            from jarvis_audio import listar_microfones

            microfones = listar_microfones()
            rotulo_aviso_microfone.setText(
                f"{len(microfones)} microfone(s) encontrados."
            )

            for microfone in microfones:
                marca = " (padrão do sistema)" if microfone["padrao"] else ""
                combo_microfone.addItem(
                    f"{microfone['nome']}{marca}", str(microfone["indice"])
                )
        except Exception as erro:
            log.exception("Não consegui listar os microfones")
            rotulo_aviso_microfone.setText(
                f"Não consegui listar os microfones ({type(erro).__name__}: {erro}). "
                "O Jarvis vai usar o padrão do sistema."
            )
        finally:
            QApplication.restoreOverrideCursor()

        escolhido = str(config.get("microfone", "") or "")
        indice_escolhido = combo_microfone.findData(escolhido)
        combo_microfone.setCurrentIndex(max(0, indice_escolhido))

        # -- motor de voz ----------------------------------------------------
        rotulo_motor = QLabel("Reconhecimento de voz:")
        combo_motor = QComboBox()
        combo_motor.addItem("Automático (com plano B)", "")

        nomes_amigaveis = {
            "google": "Google (pela internet, mais preciso)",
            "whisper": "Whisper local (offline)",
            "vosk": "Vosk local (offline)",
            "windows": "Reconhecimento do Windows (offline)",
        }

        try:
            for motor in GerenciadorDeVoz(config=config, logger=log).disponiveis():
                combo_motor.addItem(nomes_amigaveis.get(motor, motor), motor)
        except Exception:
            log.exception("Não consegui descobrir os motores de voz")

        motor_escolhido = str(config.get("motor_de_voz", "") or "")
        indice_motor = combo_motor.findData(motor_escolhido)
        combo_motor.setCurrentIndex(max(0, indice_motor))

        dica_motor = QLabel(
            "Automático usa o melhor motor disponível e, se a internet cair, "
            "passa para um offline sozinho. Para instalar o Whisper local: "
            "pip install faster-whisper"
        )
        dica_motor.setObjectName("textoPequeno")
        dica_motor.setWordWrap(True)

        checkbox_tela = QCheckBox(
            "Permitir visualização de tela quando eu pedir"
        )
        checkbox_tela.setChecked(
            config["visualizacao_de_tela_ativada"]
        )

        checkbox_filtro = QCheckBox(
            "Ativar filtro de palavrões"
        )
        checkbox_filtro.setChecked(
            config["filtro_palavroes_ativado"]
        )

        checkbox_camera = QCheckBox("Manter câmera ativa e analisar movimentos")
        checkbox_camera.setChecked(config.get("camera_ativada", True))

        checkbox_presenca = QCheckBox(
            "Bloquear computador quando eu sair da câmera (avisa 10s antes)"
        )
        checkbox_presenca.setChecked(config.get("bloquear_ao_sair", True))

        checkbox_emocoes = QCheckBox("Estimar expressões faciais pela câmera")
        checkbox_emocoes.setChecked(config.get("detectar_emocoes", True))

        checkbox_exoesqueleto = QCheckBox("Exibir exoesqueleto facial")
        checkbox_exoesqueleto.setChecked(config.get("exoesqueleto_facial", True))

        checkbox_holograma = QCheckBox("Exibir holograma na câmera")
        checkbox_holograma.setChecked(config.get("holograma_ativado", True))

        checkbox_gestos = QCheckBox("Reagir a gestos das mãos")
        checkbox_gestos.setChecked(config.get("reagir_a_gestos", True))

        salvar = QPushButton("Salvar configurações")

        def confirmar():
            config["visualizacao_de_tela_ativada"] = checkbox_tela.isChecked()
            config["filtro_palavroes_ativado"] = checkbox_filtro.isChecked()
            config["camera_ativada"] = checkbox_camera.isChecked()
            config["bloquear_ao_sair"] = checkbox_presenca.isChecked()
            config["detectar_emocoes"] = checkbox_emocoes.isChecked()
            config["exoesqueleto_facial"] = checkbox_exoesqueleto.isChecked()
            config["holograma_ativado"] = checkbox_holograma.isChecked()
            config["reagir_a_gestos"] = checkbox_gestos.isChecked()
            config["microfone"] = combo_microfone.currentData() or ""
            config["motor_de_voz"] = combo_motor.currentData() or ""

            salvar_config(config)
            log.info(
                "Configurações salvas (microfone=%r, motor de voz=%r)",
                config["microfone"],
                config["motor_de_voz"],
            )
            janela.accept()

        salvar.clicked.connect(confirmar)

        layout.addWidget(texto)
        layout.addWidget(rotulo_aviso_microfone)
        layout.addWidget(rotulo_microfone)
        layout.addWidget(combo_microfone)
        layout.addWidget(rotulo_motor)
        layout.addWidget(combo_motor)
        layout.addWidget(dica_motor)
        layout.addWidget(checkbox_tela)
        layout.addWidget(checkbox_filtro)
        layout.addWidget(checkbox_camera)
        layout.addWidget(checkbox_presenca)
        layout.addWidget(checkbox_emocoes)
        layout.addWidget(checkbox_exoesqueleto)
        layout.addWidget(checkbox_holograma)
        layout.addWidget(checkbox_gestos)
        layout.addWidget(salvar)

        janela.exec()

    def criar_icone_tray(self):
        icone = QApplication.style().standardIcon(
            QStyle.StandardPixmap.SP_ComputerIcon
        )

        self.tray = QSystemTrayIcon(icone, self)
        self.tray.setToolTip("J.A.R.V.I.S")

        menu = QMenu()

        mostrar = QAction("Mostrar painel", self)
        mostrar.triggered.connect(self.mostrar_painel)

        esconder = QAction("Esconder painel", self)
        esconder.triggered.connect(self.esconder_painel)

        sair = QAction("Fechar Jarvis", self)
        sair.triggered.connect(self.encerrar)

        menu.addAction(mostrar)
        menu.addAction(esconder)
        menu.addSeparator()
        menu.addAction(sair)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.acao_tray)
        self.tray.show()

    def acao_tray(self, motivo):
        if motivo == QSystemTrayIcon.ActivationReason.Trigger:
            self.mostrar_painel()

    def esconder_painel(self):
        self.hide()

        self.tray.showMessage(
            "Jarvis escondida",
            "Use o ícone perto do relógio para abrir o painel novamente.",
            QSystemTrayIcon.MessageIcon.Information,
            3500
        )

    def mostrar_painel(self):
        self.showMaximized()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, evento):
        """O X esconde o painel — e a câmera continua funcionando.

        Aqui estava o bug mais cruel do projeto: o X fechava os detectores do
        mediapipe e **continuava** chamando `detect()` neles a cada 220 ms,
        gerando `ValueError: Task runner is currently not running.` em loop.
        Para encerrar de verdade, use "Fechar Jarvis" na bandeja.
        """

        if self.encerrando:
            evento.accept()
            return

        evento.ignore()
        log.info("Painel escondido (use a bandeja para reabrir ou encerrar)")
        self.esconder_painel()

    def encerrar(self):
        """Encerramento de verdade, chamado pela bandeja."""

        log.info("Encerrando a interface")
        self.encerrando = True
        self.liberar_camera()
        QApplication.quit()

    def liberar_camera(self):
        """Libera câmera e detectores — uma única vez e sem estourar erro."""

        if self.recursos_liberados:
            return

        self.recursos_liberados = True

        try:
            self.timer_camera.stop()
        except Exception:
            pass

        if self.camera is not None:
            try:
                self.camera.release()
            except Exception:
                log.exception("Falha ao liberar a câmera")
            self.camera = None

        for nome in ("detector_rosto", "detector_expressoes", "detector_maos"):
            detector = getattr(self, nome, None)

            if detector is None:
                continue

            try:
                detector.close()
            except Exception:
                log.exception("Falha ao fechar %s", nome)

            setattr(self, nome, None)

        log.info("Recursos da câmera liberados")


app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)

janela = JanelaJarvis()

if janela.modelos_faltando:
    QTimer.singleShot(
        1200,
        lambda: QMessageBox.warning(
            janela,
            "Modelos da câmera",
            "Faltam arquivos de modelo para a câmera:\n\n"
            + "\n".join(f"• {nome}" for nome in janela.modelos_faltando)
            + "\n\nSem eles, os recursos de câmera ficam desativados "
            "(o resto da Jarvis funciona).\n\n"
            "Rode na pasta do projeto:  python preparar_modelos.py",
        ),
    )

janela.showMaximized()
app.aboutToQuit.connect(janela.liberar_camera)

sys.exit(app.exec())
