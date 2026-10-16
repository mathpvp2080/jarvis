"""O laço agêntico — o cérebro que decide em vez de decorar frases.

Antes, a Jarvis funcionava assim: uma lista de frases conhecidas decidia o que
fazer, e o modelo de linguagem só entrava quando *nada* era reconhecido — só
para conversar. Isso tem dois limites que aparecem no uso:

* pedido novo ("abre o chrome e pesquisa o preço do ssd") não existe na lista;
* o modelo, proibido de agir, às vezes **afirmava** ter feito algo que não fez.

Aqui o modelo recebe as ferramentas (`jarvis_ferramentas`) na chamada
``/api/chat`` do Ollama e passa a *decidir*: responder em texto ou pedir uma
ferramenta. O laço executa, devolve o resultado ao modelo e repete até ele
parar de pedir ferramentas. O que ele afirma dali para frente aconteceu de
verdade, porque foi ele que pediu e o laço que executou.

Três cuidados que este módulo existe para garantir:

* **todas** as chamadas da lista são executadas, não só a primeira — é o erro
  clássico de quem integra tool calling e o que quebra "abre o chrome **e**
  pesquisa X";
* existe **teto de rodadas** e teto de ferramentas por turno: modelo pequeno
  entra em ciclo com alguma facilidade;
* nada aqui depende de Windows, áudio ou interface: a rede entra por
  ``requisitar`` (injetável) e o relógio é o do sistema. É o que permite testar
  o laço inteiro em qualquer máquina.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from urllib.request import Request, urlopen

from jarvis_ferramentas import RegistroDeFerramentas, executar_ferramenta

# ---------------------------------------------------------------------------
# Ajustes
# ---------------------------------------------------------------------------

LIMITE_DE_RODADAS = 4
LIMITE_DE_FERRAMENTAS_POR_TURNO = 6
TEMPO_DE_RESPOSTA = 60

# Quanto tempo demora até desistir de uma ferramenta travada (clique, OCR,
# abrir programa). O laço da voz não pode ficar preso.
TEMPO_MAXIMO_DE_FERRAMENTA = 30.0

PERSONA = (
    "Você é Jarvis, a assistente pessoal de voz do usuário. Fale português "
    "brasileiro, de forma curta, direta e informal. Você tem ferramentas para "
    "agir neste computador: use-as sempre que o pedido exigir uma ação de "
    "verdade. Uma frase por resposta quando a ação for simples."
)

REGRAS = (
    "Regras:\n"
    "1. Se o pedido exige agir no computador, chame a ferramenta em vez de "
    "dizer que fez.\n"
    "2. Só afirme que algo foi feito depois de ver o resultado da ferramenta.\n"
    "3. Se o pedido tiver duas ações, chame as duas ferramentas (uma depois da "
    "outra, se a segunda depender da primeira).\n"
    "4. Se não precisar de ferramenta nenhuma, responda em texto normalmente.\n"
    "5. Se faltar informação para a ação, pergunte em uma frase curta em vez de "
    "adivinhar."
)

# Protocolo usado só quando o modelo do Ollama não sabe chamar ferramenta
# (template antigo, modelo sem suporte). Aí as ferramentas vão no texto e a
# resposta combinada é um JSON.
PROTOCOLO_DE_TEXTO = (
    "Ferramentas disponíveis:\n{guia}\n\n"
    "Se precisar de uma ferramenta, responda SOMENTE com um JSON neste "
    'formato: {{"ferramenta": "nome", "argumentos": {{...}}}}. '
    "Se não precisar, responda em texto normal."
)


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------


@dataclass
class ChamadaDeFerramenta:
    """Um pedido de ação feito pelo modelo."""

    nome: str
    argumentos: dict = field(default_factory=dict)

    def como_json(self) -> str:
        return json.dumps(
            {"ferramenta": self.nome, "argumentos": self.argumentos},
            ensure_ascii=False,
        )


@dataclass
class Passo:
    """Uma ferramenta executada, com o que entrou e o que saiu (auditoria)."""

    nome: str
    argumentos: dict
    resultado: str
    milissegundos: int = 0


@dataclass
class ResultadoDoAgente:
    """O que o turno produziu."""

    texto: str = ""
    passos: list = field(default_factory=list)
    rodadas: int = 0
    confirmacao: ChamadaDeFerramenta | None = None
    erro: str = ""
    identificador: str = ""

    @property
    def sucesso(self) -> bool:
        return not self.erro and bool(self.texto or self.confirmacao)

    @property
    def ferramentas_usadas(self) -> list:
        return [passo.nome for passo in self.passos]

    def resumo_da_auditoria(self) -> str:
        """Uma linha por ferramenta executada, para o log."""

        return "; ".join(
            f"{passo.nome}({json.dumps(passo.argumentos, ensure_ascii=False)})"
            f"->{passo.resultado[:60]} em {passo.milissegundos} ms"
            for passo in self.passos
        )


# ---------------------------------------------------------------------------
# Leitura da resposta do modelo
# ---------------------------------------------------------------------------


def extrair_chamadas(mensagem: dict) -> list:
    """Lê ``tool_calls`` da mensagem, tolerando o formato de cada versão.

    O Ollama já devolveu argumentos como dicionário e como string JSON; as duas
    formas são aceitas. Chamada sem nome é descartada.
    """

    if not isinstance(mensagem, dict):
        return []

    bruto = mensagem.get("tool_calls") or mensagem.get("function_call") or []
    chamadas = []

    if isinstance(bruto, dict):  # veio uma só, fora da lista
        bruto = [bruto]

    for item in bruto:
        if not isinstance(item, dict):
            continue

        funcao = item.get("function") if isinstance(item.get("function"), dict) else item
        nome = str(funcao.get("name") or "").strip()

        if not nome:
            continue

        argumentos = funcao.get("arguments", funcao.get("argumentos", {}))

        if isinstance(argumentos, str):
            try:
                argumentos = json.loads(argumentos) if argumentos.strip() else {}
            except ValueError:
                argumentos = {}

        if not isinstance(argumentos, dict):
            argumentos = {}

        chamadas.append(ChamadaDeFerramenta(nome, argumentos))

    return chamadas


def extrair_json_de_texto(texto: str) -> dict | None:
    """Acha o primeiro objeto JSON dentro de um texto (modo texto).

    Modelo pequeno costuma enfeitar ("Claro! {\\"ferramenta\\": ...}"), então a
    busca é pelo trecho entre a primeira chave e a última, e o que não for JSON
    é simplesmente ignorado.
    """

    if not texto:
        return None

    inicio = texto.find("{")
    fim = texto.rfind("}")

    if inicio < 0 or fim <= inicio:
        return None

    try:
        dados = json.loads(texto[inicio:fim + 1])
    except ValueError:
        return None

    return dados if isinstance(dados, dict) else None


def chamadas_do_texto(texto: str) -> list:
    """Traduz a resposta do modo texto em chamadas de ferramenta."""

    dados = extrair_json_de_texto(texto)

    if not dados:
        return []

    nome = dados.get("ferramenta") or dados.get("tool") or dados.get("nome")
    argumentos = dados.get("argumentos") or dados.get("arguments") or {}

    if not nome:
        return []

    if not isinstance(argumentos, dict):
        argumentos = {}

    return [ChamadaDeFerramenta(str(nome), argumentos)]


def parece_erro_de_ferramenta(mensagem: str) -> bool:
    """O Ollama/modelo reclamou de não aceitar ``tools``?"""

    texto = str(mensagem or "").lower()
    return (
        "does not support tools" in texto
        or "does not support tool" in texto
        or "tool" in texto and "support" in texto
    )


# ---------------------------------------------------------------------------
# Transporte
# ---------------------------------------------------------------------------


def requisitar_json(url: str, corpo: dict, timeout: float = TEMPO_DE_RESPOSTA) -> dict:
    """POST JSON com a biblioteca padrão (sem dependência nova)."""

    requisicao = Request(
        url,
        data=json.dumps(corpo).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urlopen(requisicao, timeout=timeout) as resposta:
        return json.loads(resposta.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# O agente
# ---------------------------------------------------------------------------


class Agente:
    """Laço de decisão sobre as ferramentas registradas."""

    def __init__(
        self,
        registro: RegistroDeFerramentas,
        modelo: str,
        url: str,
        log=None,
        contexto: str = "",
        max_rodadas: int = LIMITE_DE_RODADAS,
        max_ferramentas: int = LIMITE_DE_FERRAMENTAS_POR_TURNO,
        tempo_limite: float = TEMPO_DE_RESPOSTA,
        requisitar=requisitar_json,
    ) -> None:
        self.registro = registro
        self.modelo = modelo
        self.url = url
        self.log = log
        self.contexto = contexto
        self.max_rodadas = max_rodadas
        self.max_ferramentas = max_ferramentas
        self.tempo_limite = tempo_limite
        self.requisitar = requisitar

        # Vira True quando o modelo/Ollama recusa o parâmetro `tools`; a partir
        # daí o turno segue no protocolo de texto.
        self.modo_texto = False

    # -- montagem ----------------------------------------------------------

    def _sistema(self) -> str:
        partes = [PERSONA, REGRAS]

        if self.modo_texto:
            partes.append(PROTOCOLO_DE_TEXTO.format(guia=self.registro.guia_em_texto()))

        if self.contexto:
            partes.append(self.contexto)

        return "\n\n".join(partes)

    def _pedido(self, mensagens: list, com_ferramentas: bool) -> dict:
        corpo = {
            "model": self.modelo,
            "messages": mensagens,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 300},
        }

        if com_ferramentas:
            corpo["tools"] = self.registro.esquemas()

        return corpo

    def _conversar(self, mensagens: list) -> dict:
        """Uma rodada de conversa com o modelo, com o recuo de protocolo."""

        try:
            dados = self.requisitar(
                self.url,
                self._pedido(mensagens, com_ferramentas=not self.modo_texto),
                self.tempo_limite,
            )
        except Exception as erro:
            if self.modo_texto or not parece_erro_de_ferramenta(str(erro)):
                raise

            if self.log is not None:
                self.log.warning(
                    "Este modelo não aceita `tools` (%s); seguindo no protocolo de texto",
                    erro,
                )

            self.modo_texto = True
            dados = self.requisitar(
                self.url,
                self._pedido(mensagens, com_ferramentas=False),
                self.tempo_limite,
            )

        if not isinstance(dados, dict):
            raise RuntimeError("resposta do modelo fora do formato esperado")

        return dados

    # -- laço --------------------------------------------------------------

    def responder(self, pergunta: str, executar=None) -> ResultadoDoAgente:
        """Conversa até o modelo parar de pedir ferramenta.

        ``executar(nome, argumentos) -> str`` é quem roda de verdade; sem ele, a
        execução usa o próprio registro (útil em teste).
        """

        resultado = ResultadoDoAgente(identificador=uuid.uuid4().hex[:8])
        executar = executar or (
            lambda nome, argumentos: executar_ferramenta(
                self.registro, nome, argumentos, log=self.log
            )
        )

        mensagens = [
            {"role": "system", "content": self._sistema()},
            {"role": "user", "content": pergunta},
        ]

        executadas = 0

        for rodada in range(1, self.max_rodadas + 1):
            resultado.rodadas = rodada

            # O prompt do sistema é refeito a cada rodada: quando o modelo não
            # aceita `tools`, o guia das ferramentas precisa entrar no texto, e
            # a troca de protocolo acontece no meio do turno.
            mensagens[0] = {"role": "system", "content": self._sistema()}

            try:
                dados = self._conversar(mensagens)
            except Exception as erro:
                resultado.erro = f"{type(erro).__name__}: {erro}"
                if self.log is not None:
                    self.log.warning(
                        "[%s] O agente não respondeu: %s", resultado.identificador, resultado.erro
                    )
                return resultado

            mensagem = dados.get("message") or {}
            texto = str(mensagem.get("content") or "").strip()

            chamadas = extrair_chamadas(mensagem)

            if not chamadas and self.modo_texto:
                chamadas = chamadas_do_texto(texto)

                if chamadas:
                    texto = ""

            if not chamadas:
                resultado.texto = texto
                return resultado

            mensagens.append(
                {"role": "assistant", "content": texto, "tool_calls": mensagem.get("tool_calls", [])}
            )

            for chamada in chamadas:
                ferramenta = self.registro.obter(chamada.nome)

                if ferramenta is None:
                    saida = executar_ferramenta(self.registro, chamada.nome, chamada.argumentos)
                elif ferramenta.confirma:
                    # Ação que mexe no computador do usuário: para aqui e deixa
                    # o main.py perguntar. O texto do modelo não é falado.
                    resultado.confirmacao = chamada
                    return resultado
                else:
                    if executadas >= self.max_ferramentas:
                        resultado.erro = "limite de ferramentas por turno atingido"
                        return resultado

                    inicio = time.monotonic()
                    saida = executar(chamada.nome, chamada.argumentos)
                    gasto = int((time.monotonic() - inicio) * 1000)

                    resultado.passos.append(
                        Passo(chamada.nome, chamada.argumentos, str(saida), gasto)
                    )
                    executadas += 1

                    if self.log is not None:
                        self.log.info(
                            "[%s] Ferramenta %s(%s) -> %s",
                            resultado.identificador,
                            chamada.nome,
                            json.dumps(chamada.argumentos, ensure_ascii=False),
                            str(saida)[:200],
                        )

                mensagens.append({"role": "tool", "content": str(saida)})

                if chamada.nome in self.registro and self.registro.obter(chamada.nome).confirma:
                    continue

            if rodada == self.max_rodadas:
                resultado.erro = f"o modelo pediu ferramenta em {rodada} rodadas seguidas"
                return resultado

        resultado.erro = "o agente não chegou a uma resposta"
        return resultado
