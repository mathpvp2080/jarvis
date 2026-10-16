# Plano de trabalho — em pedaços pequenos

**Regra combinada (16/10/2026):** cada item abaixo é **um passo**, entregue em
**um commit** com testes e `ruff` limpos. Um passo nunca passa de ~1 arquivo novo
(+2 alterados) e de ~200 linhas, para caber no tempo de resposta de uma rodada.
Se um passo crescer, ele é cortado em dois antes de começar.

O que já está feito está marcado; o resto está na ordem de dependência.

---

## ✅ Passo 0 — Orçamento de tempo do agente
*Commit `0253484`.* Teto de 90 s por turno, teto por chamada e por ferramenta
(thread com teto), `motivo` dizendo por que parou, aviso falado em vez de espera
silenciosa. **167 testes.**

## ✅ Fase A — Cérebro com ferramentas
*Commits `6958b34` e `5391975`.* Registro de 16 ferramentas, laço agêntico,
roteamento híbrido, confirmação para ação perigosa, dois recuos.

---

## ✅ Passo 0.5 — Instalação em dois cliques
`instalar.bat` / `iniciar.bat` + `instalar_jarvis.py` (decisões testáveis).
**188 testes.**

## Fase B — Memória (6 passos)

| # | Passo | Entrega visível | Toca |
|---|---|---|---|
| B1 | Esquema SQLite + migração do `memoria_jarvis.jsonl` (sem mudar comportamento) | — (base) | `jarvis_memoria.py` novo |
| B2 | Mensagens passam a ser gravadas/lidas no SQLite, com o `.jsonl` mantido para o painel | histórico mais rápido | `jarvis_memoria.py`, `main.py` |
| B3 | Embeddings via Ollama (`nomic-embed-text`) com cache em disco e recuo por palavra quando o modelo falta | busca por sentido | `jarvis_memoria.py`, `main.py` |
| B4 | Busca semântica alimenta o contexto do agente e a conversa | "lembra daquele curso?" | `main.py` |
| B5 | Extração automática de fatos ao fim do turno ("o que descobri sobre ele?") | ela passa a te conhecer | `jarvis_memoria.py`, `main.py` |
| B6 | Compactação do histórico por resumo (o prompt para de crescer) | respostas mais rápidas | `jarvis_memoria.py`, `main.py` |

## Fase C — Voz e escuta (5 passos)

| # | Passo | Entrega visível | Toca |
|---|---|---|---|
| C1 | Motor Piper no `jarvis_tts` (com SAPI como reserva) | voz natural pt-BR | `jarvis_tts.py` |
| C2 | Script de provisionamento da voz + chave no `config_jarvis.json` | `python baixar_voz_piper.py` | script novo |
| C3 | `faster-whisper` provisionado e promovido a motor preferido offline | ouve sem internet | `jarvis_voz.py`, script |
| C4 | *Barge-in*: falar por cima corta a fala dela e atende | conversa de gente | `main.py`, `jarvis_voz.py` |
| C5 | Persona no prompt (tom, respostas curtas, humor seco) | personalidade | `main.py` |

## Fase D — Acordar de verdade (4 passos)

| # | Passo | Entrega visível | Toca |
|---|---|---|---|
| D1 | Detecção de palavra-chave local ("Jarvis") antes de transcrever | só transcreve depois do wake | `jarvis_despertar.py` novo |
| D2 | Fluxo dormindo/ouvindo ligado ao laço, com tempo de espera | ela "acorda" | `main.py` |
| D3 | Estados no painel (ocioso/ouvindo/pensando/falando/executando) com o núcleo reagindo | a interface comunica | `jarvis_tela.py` |
| D4 | Atalho global de push-to-talk | falar sem o wake | `jarvis_tela.py`, `main.py` |

## Fase E — Proatividade (5 passos)

| # | Passo | Entrega visível | Toca |
|---|---|---|---|
| E1 | Relógio interno + orçamento de interrupção (quantas por hora, nunca enquanto você digita) | base | `jarvis_proatividade.py` novo |
| E2 | Lembretes ("me lembra de X às 18h") como ferramenta do agente | lembrete falado na hora | `jarvis_proatividade.py`, `main.py` |
| E3 | Briefing do dia na primeira vez que ela te vê (hora, lembretes, recados) | bom dia de verdade | `jarvis_proatividade.py` |
| E4 | A câmera como sensor: tempo sem levantar, expressão | "90 min sentado" | `jarvis_proatividade.py`, `jarvis_tela.py` |
| E5 | Controle de frequência no painel (só os avisos que você quiser) | nada de barulho | `jarvis_tela.py` |

## Fase F — Alcance (opcional, 4 passos)

| # | Passo | Entrega visível |
|---|---|---|
| F1 | Home Assistant: ler os estados e **gerar as ferramentas** (sem escrever uma por lâmpada) |
| F2 | Gerador de imagem local (ComfyUI/A1111) ligado a `gerar_imagem` |
| F3 | Spotify API de verdade (em vez de dirigir o YouTube Music pelo navegador) |
| F4 | MCP: expor as ferramentas e aceitar servidores externos |

---

## Como eu reporto cada passo

1. Faço o passo, rodo a suíte inteira e o `ruff`.
2. Commito **só ele** (mensagem explicando o antes/depois).
3. Te digo em 3 linhas: o que mudou, o que você precisa fazer na sua máquina (se
   algo), e qual é o próximo passo — sem abrir frentes novas sem sua ordem.

## Decisão pendente sua

* Qual fase começar agora (B, C, D ou E).
* Se quer PR a cada fase ou a cada passo (hoje os commits estão no branch
  `arena/ad51c681-jarvis`, já empurrados, sem PR aberto).
