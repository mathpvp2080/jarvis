"""Registro de ferramentas — as "mãos" da Jarvis.

Uma ferramenta é uma ação que a assistente sabe fazer, descrita de um jeito que
o modelo de linguagem entende (nome, texto do que faz, parâmetros) e ligada a
uma função Python que faz de verdade.

Por que isto existe separado do `main.py`: o registro não depende de Windows,
de áudio nem da interface. Ele é **contrato puro** — entra um nome e um
dicionário de argumentos, sai um texto curto com o resultado. Isso é o que
permite

* testar as ferramentas em qualquer sistema, sem microfone nem tela;
* a mesma ferramenta ser chamada pelo modelo, pelo painel ou pela voz;
* um dia o celular executar as mesmas ferramentas: o registro vira JSON e vai
  pela rede (veja `esquemas()`), sem reescrever nada.

Formato do schema: o mesmo que o Ollama e a API da OpenAI usam
(`{"type": "function", "function": {...}}`), então não há tradução no caminho.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

# ---------------------------------------------------------------------------
# A ferramenta
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ferramenta:
    """Uma ação executável, descrita para o modelo e ligada a uma função.

    ``parametros`` é o mapa de propriedades do JSON Schema, no formato
    ``{"nome_do_argumento": {"type": "string", "description": "..."}}``.
    ``obrigatorios`` lista os que não têm valor padrão.

    ``confirma`` marca a ação que muda algo no computador do usuário de forma
    difícil de desfazer (digitar no teclado dele, fechar a Jarvis). Nesse caso o
    agente **não executa**: ele devolve a chamada e o `main.py` pergunta antes.

    ``intencao`` liga a ferramenta à intenção equivalente do reconhecedor
    antigo. É essa ponte que faz o fluxo de confirmação que já existia continuar
    valendo, sem escrever um segundo caminho.
    """

    nome: str
    descricao: str
    executar: Callable[[dict], str] | None = None
    parametros: dict = field(default_factory=dict)
    obrigatorios: tuple = ()
    confirma: bool = False
    intencao: str = ""
    origem: str = "computador"

    # Frase usada para perguntar antes de executar ("Você quer que eu <isto>?").
    # Evita montar a pergunta a partir da descrição, que é escrita para o
    # modelo, não para ser falada.
    pedido_de_confirmacao: str = ""

    # ``True`` quando a própria função já fala o resultado (é o caso das ações
    # que existiam antes do agente: "Abrindo o Chrome."). ``False`` quando a
    # ferramenta só devolve informação — aí quem fala é o modelo, e o `main.py`
    # precisa falar o texto dele para a resposta não ficar muda.
    fala_sozinha: bool = True

    def esquema(self) -> dict:
        """A ferramenta no formato que o Ollama espera em ``tools``."""

        return {
            "type": "function",
            "function": {
                "name": self.nome,
                "description": self.descricao,
                "parameters": {
                    "type": "object",
                    "properties": dict(self.parametros),
                    "required": list(self.obrigatorios),
                },
            },
        }

    def argumentos_validos(self, argumentos: dict) -> tuple[bool, str]:
        """Confere os obrigatórios antes de executar.

        Modelo pequeno erra o nome do campo com alguma frequência; melhor
        responder "faltou o nome da música" do que estourar um TypeError lá
        dentro da ação.
        """

        if not isinstance(argumentos, dict):
            return False, "os argumentos não vieram em formato de objeto"

        for nome in self.obrigatorios:
            valor = argumentos.get(nome)

            if valor is None or (isinstance(valor, str) and not valor.strip()):
                return False, f"faltou o argumento obrigatório '{nome}'"

        return True, ""

    def linha_do_guia(self) -> str:
        """Descrição em uma linha, usada no modo texto e na ajuda falada."""

        if not self.parametros:
            return f"- {self.nome}: {self.descricao}"

        campos = ", ".join(
            f"{nome}{' (obrigatório)' if nome in self.obrigatorios else ''}"
            for nome in self.parametros
        )

        return f"- {self.nome}({campos}): {self.descricao}"


# ---------------------------------------------------------------------------
# O registro
# ---------------------------------------------------------------------------


class RegistroDeFerramentas:
    """Conjunto de ferramentas disponíveis, com busca por nome."""

    def __init__(self) -> None:
        self._ferramentas: dict[str, Ferramenta] = {}

    def registrar(self, ferramenta: Ferramenta) -> Ferramenta:
        if not ferramenta.nome:
            raise ValueError("ferramenta sem nome")

        if ferramenta.nome in self._ferramentas:
            raise ValueError(f"ferramenta repetida: {ferramenta.nome}")

        if ferramenta.executar is None:
            raise ValueError(f"ferramenta sem função de execução: {ferramenta.nome}")

        self._ferramentas[ferramenta.nome] = ferramenta
        return ferramenta

    def limpar(self) -> None:
        """Esvazia o registro (usado ao remontar as ferramentas)."""

        self._ferramentas.clear()

    def obter(self, nome: str) -> Ferramenta | None:
        return self._ferramentas.get(str(nome or "").strip())

    def __contains__(self, nome: object) -> bool:
        return str(nome) in self._ferramentas

    def __len__(self) -> int:
        return len(self._ferramentas)

    def nomes(self) -> list[str]:
        return sorted(self._ferramentas)

    def todas(self) -> list[Ferramenta]:
        return [self._ferramentas[nome] for nome in self.nomes()]

    def esquemas(self) -> list[dict]:
        """Todas as ferramentas no formato do parâmetro ``tools``."""

        return [ferramenta.esquema() for ferramenta in self.todas()]

    def guia_em_texto(self) -> str:
        """Lista legível, para o modo texto e para a ferramenta de ajuda."""

        return "\n".join(ferramenta.linha_do_guia() for ferramenta in self.todas())

    def como_json(self) -> str:
        """O registro inteiro em JSON — o que o celular consumiria."""

        return json.dumps(self.esquemas(), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------


def executar_ferramenta(
    registro: RegistroDeFerramentas,
    nome: str,
    argumentos: dict,
    log=None,
) -> str:
    """Roda uma ferramenta e devolve o texto do resultado (nunca levanta).

    A exceção nunca escapa: o agente recebe a mensagem de erro como resultado
    da ferramenta e pode explicar ao usuário ou tentar outra coisa. Um
    ``TypeError`` no meio de um clique não pode derrubar o laço da voz.
    """

    ferramenta = registro.obter(nome)

    if ferramenta is None:
        parecidas = [
            conhecida for conhecida in registro.nomes()
            if nome and (nome in conhecida or conhecida in nome)
        ]
        dica = f" Ferramentas parecidas: {', '.join(parecidas)}." if parecidas else ""
        return f"Ferramenta desconhecida: {nome}.{dica}"

    valido, problema = ferramenta.argumentos_validos(argumentos)

    if not valido:
        return f"Não executei {nome}: {problema}."

    try:
        resultado = ferramenta.executar(dict(argumentos or {}))
    except Exception as erro:  # a ferramenta nunca derruba quem chamou
        if log is not None:
            log.exception("Falha ao executar a ferramenta %s", nome)
        return f"A ferramenta {nome} falhou ({type(erro).__name__}: {erro})."

    texto = str(resultado or "").strip()

    if not texto:
        return f"{nome} executado (sem detalhes)."

    return texto
