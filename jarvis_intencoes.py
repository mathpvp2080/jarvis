"""Camada de intenções: entende o que você fala de verdade.

Por que isto existe
-------------------
O roteamento antigo era uma sequência de `if "..." in comando`: só funcionava
se você falasse exatamente a frase cadastrada. Era por isso que pedidos
naturais como *"ta vendo minha tela?"*, *"para ligar a câmera"* ou
*"tira holograma para mim"* caíam no modelo de linguagem, que respondia
*"você precisa usar um comando específico da Jarvis"*.

O que este módulo acrescenta
----------------------------
* **Sinônimos de verbo**: abre/abra/inicia/executa/roda → `abrir`.
* **Palavras de enchimento** ignoradas na comparação ("abre **o** chrome",
  "**me** mostra **a** tela", "**por favor**").
* **Parecença (fuzzy)**: frases tortas do reconhecimento de voz ainda são
  reconhecidas, com faixa de dúvida — entre `LIMIAR_DE_CONFIRMACAO` e
  `LIMIAR_DE_EXECUCAO` o Jarvis pergunta "você quis dizer...?" antes de agir.
* **Nada é adivinhado**: fora das faixas, a frase vai para o modelo de
  linguagem como conversa livre (como sempre foi).

O módulo é puro Python — dá para testar tudo isso sem Windows, sem microfone
e sem interface.
"""

from __future__ import annotations

import difflib
import unicodedata
from dataclasses import dataclass

LIMIAR_DE_EXECUCAO = 0.85
LIMIAR_DE_CONFIRMACAO = 0.68


@dataclass(frozen=True)
class Intencao:
    """O que foi entendido de uma frase."""

    nome: str
    confianca: float
    argumento: str = ""
    gatilho: str = ""
    frase: str = ""

    @property
    def precisa_confirmar(self) -> bool:
        return self.confianca < LIMIAR_DE_EXECUCAO


def normalizar(texto: str) -> str:
    """Minúsculas, sem acentos e sem pontuação."""

    texto = unicodedata.normalize("NFD", str(texto or "").lower())

    texto = "".join(
        letra for letra in texto
        if unicodedata.category(letra) != "Mn"
    )

    texto = "".join(
        letra if letra.isalnum() else " "
        for letra in texto
    )

    return " ".join(texto.split())


# Verbos equivalentes viram um só, para que "abre a steam" e "abrir a steam"
# cheguem ao mesmo lugar. (Cuidado ao acrescentar: "por" e "para" são também
# preposições — "por" ficou de fora de propósito.)
EQUIVALENTES_DE_VERBO = {
    "abre": "abrir", "abra": "abrir", "abri": "abrir", "abrir": "abrir",
    "inicia": "abrir", "inicie": "abrir", "iniciar": "abrir",
    "executa": "abrir", "execute": "abrir", "executar": "abrir",
    "roda": "abrir", "rode": "abrir", "rodar": "abrir",
    "lanca": "abrir", "lance": "abrir", "lancar": "abrir",
    "chama": "abrir", "chame": "abrir", "chamar": "abrir",
    "entra": "abrir", "entre": "abrir",

    "toca": "tocar", "toque": "tocar", "tocar": "tocar",
    "coloca": "tocar", "coloque": "tocar", "colocar": "tocar",
    "bota": "tocar", "bote": "tocar", "botar": "tocar",
    "poe": "tocar", "poem": "tocar",
    "reproduz": "tocar", "reproduza": "tocar", "reproduzir": "tocar",
    "escuta": "tocar", "escute": "tocar",

    "pesquisa": "pesquisar", "pesquise": "pesquisar", "pesquisar": "pesquisar",
    "procura": "pesquisar", "procure": "pesquisar", "procurar": "pesquisar",
    "busca": "pesquisar", "busque": "pesquisar", "buscar": "pesquisar",

    "liga": "ligar", "ligue": "ligar", "ligar": "ligar",
    "ativa": "ligar", "ative": "ligar", "ativar": "ligar",
    "acende": "ligar", "acenda": "ligar", "acender": "ligar",

    "desliga": "desligar", "desligue": "desligar", "desligar": "desligar",
    "desativa": "desligar", "desative": "desligar", "desativar": "desligar",
    "esconde": "esconder", "esconda": "esconder", "esconder": "esconder",
    "oculta": "esconder", "oculte": "esconder", "ocultar": "esconder",
    "fecha": "fechar", "feche": "fechar", "fechar": "fechar",
    "tira": "remover", "tire": "remover", "remover": "remover",
    "remove": "remover", "remova": "remover",

    "corrige": "corrigir", "corrija": "corrigir", "corrigir": "corrigir",
    "mostra": "mostrar", "mostre": "mostrar", "mostrar": "mostrar",
    "exibe": "mostrar", "exiba": "mostrar", "exibir": "mostrar",
    "clica": "clicar", "clique": "clicar", "clicar": "clicar",

    "aumenta": "aumentar", "aumente": "aumentar", "aumentar": "aumentar",
    "sobe": "aumentar", "suba": "aumentar", "subir": "aumentar",
    "diminui": "diminuir", "diminua": "diminuir", "diminuir": "diminuir",
    "abaixa": "diminuir", "abaixe": "diminuir", "abaixar": "diminuir",
    "baixa": "diminuir", "baixe": "diminuir", "baixar": "diminuir",

    "para": "pausar", "pare": "pausar", "pausa": "pausar",
    "pause": "pausar", "pausar": "pausar",

    "cancela": "cancelar", "cancele": "cancelar", "cancelar": "cancelar",
    "cria": "gerar", "crie": "gerar", "criar": "gerar",
    "gere": "gerar", "gerar": "gerar",
    "desenha": "gerar", "desenhe": "gerar", "desenhar": "gerar",
}

# Palavras que não mudam a intenção ("abre *o* chrome", "*me* mostra *a* tela",
# "*por favor*"). São ignoradas apenas na hora de comparar.
ENCHIMENTO = {
    "o", "a", "os", "as", "um", "uma", "uns", "umas",
    "me", "meu", "minha", "meus", "minhas", "seu", "sua",
    "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas",
    "para", "pra", "pro", "por", "com", "sem", "ao", "aos",
    "porfavor", "favor", "gentileza",
    "ai", "entao", "agora", "so", "apenas", "aqui", "la", "ja",
    "ei", "oi", "ola", "hey",
}

# "pode abrir o chrome" é pedido, não confirmação.
PEDIDOS_EDUCADOS = {"pode", "poderia", "quero", "queria", "gostaria", "preciso", "manda", "mande"}

AFIRMACOES = {"sim", "confirmar", "confirmo", "confirma", "isso mesmo", "claro", "beleza", "ok", "esta certo", "tudo bem", "pode sim", "pode clicar", "manda ver", "isso"}
NEGACOES = {"nao", "nao quero", "deixa", "deixa pra la", "nao e isso", "errado", "cancelar", "cancela"}

# Palavras que, na frase, significam "desligar/esconder".
PALAVRAS_DE_DESLIGAR = frozenset({
    "desliga", "desligue", "desligar", "desligado", "desligada",
    "desativa", "desative", "desativar", "desativado",
    "esconde", "esconda", "esconder", "escondido",
    "oculta", "oculte", "ocultar", "oculto",
    "fecha", "feche", "fechar", "fechado",
    "tira", "tire", "remover", "remove", "remova", "removido",
    "pausar", "pare", "pausa",
})

# Para a parecença valer, é preciso casar alguma palavra "de conteúdo" —
# "quero ver" em comum não faz uma pergunta sobre holograma.
VERBOS_DE_LIGACAO = frozenset({
    "ver", "ve", "olha", "olhar", "fala", "falar", "diz", "dizer",
    "faz", "fazer", "pode", "poderia", "sabe", "saber",
    "entende", "entender", "quero", "queria", "queria",
})

PALAVRAS_SEM_CONTEUDO = (
    ENCHIMENTO
    | PEDIDOS_EDUCADOS
    | VERBOS_DE_LIGACAO
    | frozenset(EQUIVALENTES_DE_VERBO.values())
)


@dataclass(frozen=True)
class _Definicao:
    nome: str
    gatilhos: tuple = ()
    verbos: tuple = ()
    descricao: str = ""
    so_frase_inteira: bool = False


# --------------------------------------------------------------------------
# Catálogo de intenções
# --------------------------------------------------------------------------

DEFINICOES = (
    _Definicao(
        nome="sair",
        gatilhos=(
            "sair", "tchau", "tchau jarvis", "adeus", "ate logo",
            "fechar jarvis", "encerrar jarvis", "pode fechar", "pode sair",
            "desligar jarvis", "desligar assistente", "encerrar", "encerrar assistente",
        ),
        descricao="encerrar o Jarvis",
    ),
    _Definicao(
        nome="aprender",
        gatilhos=("quando eu disser", "aprenda que", "aprende que", "aprenda isso", "decore que"),
        descricao="aprender um comando",
    ),
    _Definicao(
        nome="hora",
        gatilhos=(
            "que horas sao", "que hora sao", "que horas", "que hora",
            "me diga as horas", "me diga a hora", "horas agora", "hora agora",
            "qual a hora", "qual e a hora", "horas por favor", "diga as horas",
        ),
        descricao="dizer as horas",
    ),
    _Definicao(
        nome="descrever_tela",
        gatilhos=(
            "ler minha tela", "leia minha tela", "le minha tela", "ler a tela",
            "descrever minha tela", "descreva minha tela", "descreva a tela",
            "o que tem na minha tela", "o que esta na minha tela",
            "o que tem na tela", "o que esta na tela",
            "o que estou vendo", "o que eu estou vendo", "o que vejo",
            "ta vendo minha tela", "esta vendo minha tela", "esta vendo a tela",
            "voce esta vendo minha tela", "ve minha tela", "olha minha tela",
            "olha a minha tela", "descreva o que esta na minha tela",
            "descreva o que esta na tela", "olha o que esta na tela",
            "o que aparece na tela", "consegue ver minha tela",
        ),
        descricao="descrever o que está na sua tela",
    ),
    _Definicao(
        nome="corrigir_e_colar",
        gatilhos=("corrigir e colar", "corrija e cole", "corrige e cola", "corrigir e cola", "corrija e colar"),
        descricao="corrigir o texto copiado e colar",
    ),
    _Definicao(
        nome="corrigir_texto",
        gatilhos=(
            "corrigir texto copiado", "corrija texto copiado",
            "corrigir o texto copiado", "corrigir texto", "corrige o texto",
            "corrigir o que eu copiei", "corrigir o texto que copiei",
        ),
        descricao="corrigir o texto copiado",
    ),
    _Definicao(
        nome="esconder_painel",
        gatilhos=(
            "esconder painel", "esconder a janela", "esconder a interface",
            "fechar painel", "fechar a janela", "esconder jarvis",
            "sumir com o painel", "minimizar", "esconder tudo",
        ),
        descricao="esconder o painel",
    ),
    _Definicao(
        nome="holograma",
        gatilhos=(
            "holograma", "quero um holograma", "quero ver o holograma",
            "mostrar o holograma", "tira o holograma", "tira holograma",
            "tirar o holograma", "holograma ligado", "holograma desligado",
            "liga o holograma", "ligar o holograma", "desliga o holograma",
            "desligar o holograma", "ativar o holograma", "desativar o holograma",
            "cubo holografico", "holograma 3d",
        ),
        descricao="mostrar ou esconder o holograma",
    ),
    _Definicao(
        nome="camera",
        gatilhos=(
            "ligar a camera", "ativa a camera", "ativar a camera",
            "desligar a camera", "desativar a camera", "ligar camera",
            "desligar camera", "abrir a camera", "abrir camera",
            "fechar a camera", "desligar a webcam", "ligar a webcam",
            "ligar a visao", "desligar a visao",
        ),
        descricao="ligar ou desligar a câmera",
    ),
    _Definicao(
        nome="musica_controle",
        gatilhos=(
            "aumentar volume", "sobe o som", "subir o som", "aumentar o som",
            "som mais alto", "volume mais alto", "volume acima",
            "diminuir volume", "abaixa o som", "abaixar o som",
            "diminuir o som", "som mais baixo", "volume mais baixo", "volume abaixo",
            "silenciar", "mutar", "mudo", "tirar o som", "sem som",
            "pausar musica", "parar a musica", "continuar musica",
            "reproduzir musica", "dar play", "proxima musica", "proxima faixa",
            "pular musica", "pular faixa", "musica anterior", "faixa anterior",
            "voltar musica", "volta a musica anterior", "colocar a musica de novo",
        ),
        descricao="controlar a música",
    ),
    _Definicao(
        nome="abrir_youtube_music",
        gatilhos=(
            "abrir youtube music", "abrir youtube musica", "youtube music",
            "youtube musica", "abrir o site do youtube music",
        ),
        descricao="abrir o YouTube Music",
    ),
    _Definicao(
        nome="abrir_navegador",
        gatilhos=("abrir navegador", "abrir o google", "abrir o site", "abrir a internet"),
        descricao="abrir o navegador",
    ),
    _Definicao(
        nome="tocar_musica",
        verbos=("tocar",),
        gatilhos=("tocar musica", "tocar uma musica", "tocar no youtube", "tocar no youtube music"),
        descricao="tocar música",
    ),
    _Definicao(
        nome="pesquisar",
        verbos=("pesquisar",),
        descricao="pesquisar na internet",
    ),
    _Definicao(
        nome="clicar_em",
        verbos=("clicar",),
        descricao="clicar em algo na tela",
    ),
    _Definicao(
        nome="gerar_imagem",
        verbos=("gerar",),
        descricao="gerar uma imagem",
    ),
    _Definicao(
        nome="abrir_aplicativo",
        verbos=("abrir",),
        descricao="abrir um programa",
    ),
    _Definicao(
        nome="confirmar",
        gatilhos=tuple(sorted(AFIRMACOES)),
        descricao="confirmar",
        so_frase_inteira=True,
    ),
    _Definicao(
        nome="cancelar",
        gatilhos=tuple(sorted(NEGACOES)),
        descricao="cancelar",
        so_frase_inteira=True,
    ),
)

DEFINICOES_POR_NOME = {definicao.nome: definicao for definicao in DEFINICOES}


# --------------------------------------------------------------------------
# Comparação
# --------------------------------------------------------------------------

def _canonicalizar(texto_normalizado: str) -> str:
    return " ".join(
        EQUIVALENTES_DE_VERBO.get(palavra, palavra)
        for palavra in texto_normalizado.split()
    )


def _sem_enchimento(texto_canonico: str) -> str:
    palavras = [
        palavra for palavra in texto_canonico.split()
        if palavra not in ENCHIMENTO
    ]
    return " ".join(palavras) if palavras else texto_canonico


def _tirar_pedido_educado(texto_canonico: str) -> str:
    palavras = texto_canonico.split()

    while palavras and palavras[0] in PEDIDOS_EDUCADOS:
        palavras = palavras[1:]

    return " ".join(palavras)


NEGACOES_INICIAIS = ("nao", "nunca", "nem")


def _tirar_negacao(texto: str) -> str:
    palavras = texto.split()

    while palavras and palavras[0] in NEGACOES_INICIAIS:
        palavras = palavras[1:]

    return " ".join(palavras)


def _preparar_gatilho(gatilho: str) -> str:
    return _sem_enchimento(_canonicalizar(normalizar(gatilho)))


GATILHOS_PREPARADOS = {
    definicao.nome: [
        (gatilho, _preparar_gatilho(gatilho))
        for gatilho in definicao.gatilhos
    ]
    for definicao in DEFINICOES
}


def _parecenca_por_janela(texto: str, gatilho: str) -> float:
    """Maior parecença entre o gatilho e uma janela do mesmo tamanho no texto.

    A janela desliza no lado mais longo, então a frase pode ser maior
    ("jarvis, aumenta o volume aí") ou menor ("horas são") que o gatilho.
    """

    palavras_texto = texto.split()
    palavras_gatilho = gatilho.split()

    if not palavras_texto or not palavras_gatilho:
        return 0.0

    # Gatilho de uma palavra só não entra no fuzzy: parecença entre palavras
    # soltas gera falsos positivos ("piada" parecido com "ligada").
    if len(palavras_gatilho) == 1:
        return 0.0

    if abs(len(palavras_texto) - len(palavras_gatilho)) > 2:
        return 0.0

    # Precisa casar alguma palavra de conteúdo: "quero ver" em comum não faz
    # uma pergunta sobre holograma.
    conteudo_do_gatilho = [
        palavra for palavra in palavras_gatilho
        if palavra not in PALAVRAS_SEM_CONTEUDO
    ]
    palavras_texto_conjunto = set(palavras_texto)
    casadas = [
        palavra for palavra in conteudo_do_gatilho
        if palavra in palavras_texto_conjunto
    ]

    if not casadas:
        return 0.0

    if len(casadas) * 2 < len(conteudo_do_gatilho):
        return 0.0

    if len(palavras_texto) >= len(palavras_gatilho):
        maior, menor = palavras_texto, palavras_gatilho
    else:
        maior, menor = palavras_gatilho, palavras_texto

    trecho = " ".join(menor)
    melhor = 0.0

    for inicio in range(len(maior) - len(menor) + 1):
        janela = " ".join(maior[inicio:inicio + len(menor)])
        melhor = max(melhor, difflib.SequenceMatcher(None, janela, trecho).ratio())

    return melhor


def reconhecer(texto: str) -> Intencao | None:
    """Devolve a intenção mais provável da frase, ou ``None``.

    Escalas de confiança:

    * ``1.00`` — a frase inteira é um comando cadastrado;
    * ``0.95`` — verbo conhecido com argumento ("abrir steam");
    * ``0.90`` — o comando aparece dentro da frase ("jarvis, aumenta o volume");
    * ``0.68–0.85`` — parecida com um comando: vale perguntar antes de agir;
    * ``0.72`` — verbo conhecido sem argumento ("abrir... abrir o quê?").
    """

    frase = normalizar(texto)

    if not frase:
        return None

    canonico = _canonicalizar(frase)
    enxuto = _sem_enchimento(canonico)
    enxuto_sem_educacao = _sem_enchimento(_tirar_pedido_educado(_tirar_negacao(canonico)))
    negou = bool(enxuto.split()) and enxuto.split()[0] in NEGACOES_INICIAIS

    candidatos: list[Intencao] = []

    for definicao in DEFINICOES:
        for _gatilho_original, gatilho in GATILHOS_PREPARADOS[definicao.nome]:
            if not gatilho:
                continue

            # Gatilho que virou uma palavra só porque o enchimento saiu
            # ("horas por favor" -> "horas") só vale como frase inteira;
            # senão "quero ver as horas" casaria por engano.
            if definicao.so_frase_inteira or (
                len(gatilho.split()) == 1 and len(_gatilho_original.split()) > 1
            ):
                if enxuto == gatilho:
                    candidatos.append(Intencao(definicao.nome, 1.0, "", gatilho, frase))
                continue

            if enxuto == gatilho:
                candidatos.append(Intencao(definicao.nome, 1.0, "", gatilho, frase))
                continue

            if gatilho in enxuto:
                candidatos.append(Intencao(definicao.nome, 0.90, "", gatilho, frase))
                continue

            parecida = _parecenca_por_janela(enxuto, gatilho)

            if parecida >= LIMIAR_DE_CONFIRMACAO:
                candidatos.append(Intencao(definicao.nome, parecida, "", gatilho, frase))

        if definicao.verbos:
            palavras = enxuto_sem_educacao.split()

            if palavras and palavras[0] in definicao.verbos:
                argumento = " ".join(palavras[1:]).strip()
                confianca = 0.95 if argumento else 0.72
                candidatos.append(
                    Intencao(definicao.nome, confianca, argumento, palavras[0], frase)
                )

    if not candidatos:
        return None

    # "não quero abrir o chrome" não pode abrir o Chrome sem perguntar: o
    # verbo continua sendo entendido, mas nada é executado sem confirmação.
    if negou:
        candidatos = [
            Intencao(
                intencao.nome,
                0.80 if intencao.confianca >= 0.95 else min(intencao.confianca, 0.75),
                intencao.argumento,
                intencao.gatilho,
                intencao.frase,
            )
            for intencao in candidatos
        ]

    # Empate: ganha a maior confiança; depois o gatilho mais longo (mais
    # específico), para que "abrir youtube music" não vire "abrir aplicativo".
    candidatos.sort(
        key=lambda intencao: (
            round(intencao.confianca, 3),
            len(intencao.gatilho.split()),
        ),
        reverse=True,
    )

    return candidatos[0]


def descrever(intencao: Intencao) -> str:
    """Frase curta para pedir confirmação: "Você quis dizer ...?"."""

    definicao = DEFINICOES_POR_NOME.get(intencao.nome)
    base = definicao.descricao if definicao else intencao.nome

    if intencao.argumento and intencao.nome == "abrir_aplicativo":
        return f"abrir o programa {intencao.argumento}"

    if intencao.argumento:
        return f"{base} ({intencao.argumento})"

    return base


def quer_ligar(intencao: Intencao) -> bool:
    """Para as intenções de ligar/desligar (holograma, câmera).

    A checagem usa a frase original (sem acento, sem enchimento) em vez da
    versão com verbos trocados: "para ligar a câmera" tem "para" de
    preposição, e a troca para "pausar" inverteria a resposta.
    """

    palavras = _sem_enchimento(normalizar(intencao.frase)).split()

    return not any(palavra in PALAVRAS_DE_DESLIGAR for palavra in palavras)
