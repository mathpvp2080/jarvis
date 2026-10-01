import json
import html
import math
import ctypes
import os
import sys
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

import psutil
import cv2
from mediapipe import Image as MpImage, ImageFormat as MpImageFormat
from mediapipe.tasks.python import BaseOptions, vision

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
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


PASTA = Path(__file__).resolve().parent
ARQUIVO_MEMORIA = PASTA / "memoria_jarvis.json"
ARQUIVO_CONFIG = PASTA / "config_jarvis.json"
ARQUIVO_COMANDOS_INTERFACE = PASTA / "comandos_interface.json"
ARQUIVO_EVENTOS_INTERFACE = PASTA / "eventos_interface.json"


def carregar_config():
    padrao = {
        "visualizacao_de_tela_ativada": False,
        "filtro_palavroes_ativado": False,
        "camera_ativada": True,
        "bloquear_ao_sair": True,
        "detectar_emocoes": True,
        "exoesqueleto_facial": True,
        "holograma_ativado": True,
        "reagir_a_gestos": True
    }

    try:
        if ARQUIVO_CONFIG.exists():
            dados = json.loads(ARQUIVO_CONFIG.read_text(encoding="utf-8"))

            if isinstance(dados, dict):
                padrao.update(dados)
    except (json.JSONDecodeError, OSError):
        pass

    return padrao


def salvar_config(config):
    ARQUIVO_CONFIG.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def carregar_historico():
    if not ARQUIVO_MEMORIA.exists():
        return "Jarvis: Ainda não há conversas salvas."

    try:
        memorias = json.loads(
            ARQUIVO_MEMORIA.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError):
        return "Jarvis: Não foi possível ler a memória."

    if not isinstance(memorias, list) or not memorias:
        return "Jarvis: Ainda não há conversas salvas."

    linhas = []

    for item in memorias[-50:]:
        if not isinstance(item, dict):
            continue
        tipo = item.get("tipo", "Jarvis")
        texto = item.get("texto", "")
        data = item.get("data", "")

        if texto:
            linhas.append(f"[{data}] {tipo}: {texto}")

    return "\n\n".join(linhas)


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
        self.frame_anterior = None
        self.detector_rosto = vision.FaceDetector.create_from_options(
            vision.FaceDetectorOptions(
                base_options=BaseOptions(
                    model_asset_path=str(PASTA / "blaze_face_short_range.tflite")
                ),
                running_mode=vision.RunningMode.IMAGE,
                min_detection_confidence=0.65
            )
        )
        self.detector_expressoes = vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(
                base_options=BaseOptions(
                    model_asset_path=str(PASTA / "face_landmarker.task")
                ),
                running_mode=vision.RunningMode.IMAGE,
                num_faces=1,
                output_face_blendshapes=True
            )
        )
        self.detector_maos = vision.HandLandmarker.create_from_options(
            vision.HandLandmarkerOptions(
                base_options=BaseOptions(
                    model_asset_path=str(PASTA / "hand_landmarker.task")
                ),
                running_mode=vision.RunningMode.IMAGE,
                num_hands=2,
                min_hand_detection_confidence=0.65,
                min_hand_presence_confidence=0.65,
                min_tracking_confidence=0.6
            )
        )
        self.ultimo_bloqueio = 0
        self.ultimo_rosto = None
        self.rosto_confirmado = False
        self.emocao_atual = "indisponível"
        self.camera_expandida = False
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
        self.layout_navegador = layout_navegador
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

    def processar_pagina_navegador(self, carregou):
        self.processar_pagina_aba(self.navegador, carregou)

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
        self.processar_eventos_holograma()

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

    def atualizar_camera(self):
        config = carregar_config()

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

        frame = cv2.flip(frame, 1)
        movimento = self.detectar_movimento(frame)
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

        if tem_rosto:
            self.ultimo_rosto = datetime.now().timestamp()
            self.rosto_confirmado = True
            self.desenhar_rosto(frame, resultado_rosto.detections)

            if config.get("detectar_emocoes", True) or config.get("exoesqueleto_facial", True):
                resultado_expressao = self.detector_expressoes.detect(
                    MpImage(
                        image_format=MpImageFormat.SRGB,
                        data=imagem_rgb
                    )
                )
                if config.get("exoesqueleto_facial", True):
                    for landmarks in resultado_expressao.face_landmarks:
                        self.desenhar_exoesqueleto_facial(frame, landmarks)

                if config.get("detectar_emocoes", True):
                    emocao = self.estimar_emocao(resultado_expressao)
                    self.emocao_atual = emocao

            

        if config.get("reagir_a_gestos", True):
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

        if self.holograma_visivel and config.get("holograma_ativado", True):
            self.desenhar_holograma_67(frame)

        tempo_sem_rosto = (
            None if self.ultimo_rosto is None
            else agora - self.ultimo_rosto
        )

        if (
            self.rosto_confirmado and
            tempo_sem_rosto is not None and
            tempo_sem_rosto >= 8 and
            config.get("bloquear_ao_sair", True)
        ):
            if agora - self.ultimo_bloqueio > 30:
                self.ultimo_bloqueio = agora
                subprocess.Popen(
                    ["rundll32.exe", "user32.dll,LockWorkStation"],
                    creationflags=subprocess.CREATE_NO_WINDOW
                )

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

    def processar_eventos_holograma(self):
        if not ARQUIVO_EVENTOS_INTERFACE.exists():
            return

        try:
            eventos = json.loads(
                ARQUIVO_EVENTOS_INTERFACE.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError):
            return

        if not isinstance(eventos, list):
            return

        try:
            ARQUIVO_EVENTOS_INTERFACE.write_text("[]", encoding="utf-8")
        except OSError:
            pass

        for evento in eventos:
            if not isinstance(evento, dict):
                continue

            if evento.get("tipo") == "mostrar_holograma":
                self.holograma_visivel = True

            if evento.get("tipo") == "navegar_navegador":
                url = evento.get("dados", {}).get("url", "")
                if url:
                    self.navegador.setUrl(QUrl(url))

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
        self.botao_expandir_camera.setText("Expandir câmera")

    def fechar_camera_cheia(self):
        if self.janela_camera_cheia is not None:
            self.janela_camera_cheia.close()

    def palma_virada_para_cima(self, mao):
        dedos_estendidos = sum(
            mao[ponta].y < mao[articulacao].y
            for ponta, articulacao in [(8, 6), (12, 10), (16, 14), (20, 18)]
        )
        palma_aberta = dedos_estendidos >= 3
        palma_inclinada = mao[12].y < mao[0].y + 0.12
        return palma_aberta and palma_inclinada

    def atualizar_controle_holograma(self, mao, dimensoes_frame):
        distancia_pinca = max(
            ((mao[4].x - mao[8].x) ** 2 + (mao[4].y - mao[8].y) ** 2) ** 0.5,
            0.001
        )
        pinca_ativa = distancia_pinca < 0.16

        if not pinca_ativa:
            self.mao_segura_holograma = False
            self.centro_mao_anterior = None
            self.distancia_mao_anterior = None
            return

        centro_x = mao[8].x
        centro_y = mao[8].y

        if self.centro_mao_anterior is not None:
            delta_x = centro_x - self.centro_mao_anterior[0]
            delta_y = centro_y - self.centro_mao_anterior[1]
            self.holograma_rotacao_y = (self.holograma_rotacao_y + delta_x * 720) % 360
            self.holograma_rotacao_x = (self.holograma_rotacao_x + delta_y * 360) % 360

        if self.distancia_mao_anterior is not None:
            variacao = distancia_pinca / self.distancia_mao_anterior
            self.holograma_escala = max(
                0.55,
                min(2.2, self.holograma_escala * variacao)
            )

        self.mao_segura_holograma = True
        self.centro_mao_anterior = (centro_x, centro_y)
        self.distancia_mao_anterior = distancia_pinca

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

    def detectar_movimento(self, frame):
        cinza = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        cinza = cv2.GaussianBlur(cinza, (21, 21), 0)

        if self.frame_anterior is None:
            self.frame_anterior = cinza
            return False

        diferenca = cv2.absdiff(self.frame_anterior, cinza)
        self.frame_anterior = cinza
        _, mascara = cv2.threshold(diferenca, 25, 255, cv2.THRESH_BINARY)
        return cv2.countNonZero(mascara) > frame.shape[0] * frame.shape[1] * 0.025

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
        self.enviar_tecla_windows("{PGUP}" if quantidade > 0 else "{PGDN}")

    def navegar_historico(self, direcao):
        self.enviar_tecla_windows("%{LEFT}" if direcao == "voltar" else "%{RIGHT}")

    def alternar_reproducao(self):
        self.enviar_tecla_windows("{SPACE}")

    def enviar_tecla_windows(self, tecla):
        comando = f"$wshell = New-Object -ComObject WScript.Shell; $wshell.SendKeys('{tecla}')"
        subprocess.Popen(["powershell.exe", "-NoProfile", "-Command", comando], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)

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
        if not ARQUIVO_MEMORIA.exists():
            return []

        try:
            dados = json.loads(
                ARQUIVO_MEMORIA.read_text(encoding="utf-8")
            )

            if not isinstance(dados, list):
                return []
            return [item for item in dados if isinstance(item, dict)]
        except (json.JSONDecodeError, OSError):
            return []

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
            "Descreva a imagem:"
        )

        if confirmou and texto.strip():
            self.enfileirar_comando(f"gerar imagem {texto.strip()}")

    def enfileirar_comando(self, texto):
        comandos = []

        if ARQUIVO_COMANDOS_INTERFACE.exists():
            try:
                dados = json.loads(
                    ARQUIVO_COMANDOS_INTERFACE.read_text(encoding="utf-8")
                )
                if isinstance(dados, list):
                    comandos = dados
            except (json.JSONDecodeError, OSError):
                comandos = []

        comandos.append({"texto": texto, "data": datetime.now().isoformat()})
        ARQUIVO_COMANDOS_INTERFACE.write_text(
            json.dumps(comandos, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

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
            "As opções são salvas no seu computador e valem para a próxima resposta da Jarvis."
        )
        texto.setWordWrap(True)

        config = carregar_config()

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

        checkbox_presenca = QCheckBox("Bloquear computador quando eu sair da câmera")
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

            salvar_config(config)
            janela.accept()

        salvar.clicked.connect(confirmar)

        layout.addWidget(texto)
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
        sair.triggered.connect(QApplication.quit)

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
        evento.ignore()
        self.liberar_camera()
        self.esconder_painel()

    def liberar_camera(self):
        if self.camera is not None:
            self.camera.release()
            self.camera = None

        self.detector_rosto.close()
        self.detector_expressoes.close()
        self.detector_maos.close()



app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)

janela = JanelaJarvis()
janela.showMaximized()
app.aboutToQuit.connect(janela.liberar_camera)

sys.exit(app.exec())