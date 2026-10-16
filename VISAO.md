# Visão — a Jarvis como o Jarvis do Homem de Ferro

**Data:** 12/10/2026
**Base:** pesquisa em 7 projetos open-source de "Jarvis" ativos em 2026 + 3 guias de
arquitetura local (ver *Fontes* no fim), comparados com o estado atual deste repositório.

---

## 1. O que "um Jarvis de verdade" é, hoje

Todos os projetos sérios convergem para a mesma forma. Não é um chatbot com comandos:
é um **agente** — um laço em que o modelo *decide* o que fazer, chama ferramentas,
lê o resultado e responde.

```
   wake word local ("Jarvis")          ← sempre ouvindo, sem transcrever tudo
        ↓
   VAD + endpointing (silero)          ← detecta fim de fala; permite interromper
        ↓
   STT local (faster-whisper)          ← nada de depender do Google
        ↓
   memória semântica (embedding)       ← recupera fatos e conversa antiga
        ↓
   LLM com TOOL CALLING  ←──────────┐  ← o cérebro ROTEIA, não decora frase
        ↓                           │
   executa a ferramenta             │  abrir app, tocar, pesquisar, ler tela,
        ↓                           │  clicar, casa, lembrete, gerar imagem…
   resultado volta pro modelo ──────┘  (laço agêntico, várias rodadas)
        ↓
   TTS neural local (Piper/Kokoro)     ← voz natural, não a SAPI do Windows
        ↓
   grava na memória + audit trail
```

Em volta disso, três coisas que fazem parecer o Jarvis do filme:

* **máquina de estados visível** — `DORMINDO → OUVINDO → PENSANDO → FALANDO`, com
  orbe/HUD reagindo (o painel mostra o que ela está fazendo, não só texto);
* **proatividade** — um *heartbeat* que age sem ser chamado: bom-dia com resumo,
  lembrete na hora certa, "faz 50 min que você está nessa janela", aviso se a
  postura/expressão mudou;
* **camada de segurança** — ação perigosa pede confirmação (notificação + hotkey),
  tudo registrado num *audit trail* reproduzível, com *correlation id* ligando
  wake → STT → LLM → ferramenta → TTS no log.

E um detalhe de arquitetura que todos os projetos maduros têm: **roteador híbrido**.
Uma camada determinística (regex/intenções) para o que tem que ser instantâneo
("aumenta o volume" não pode esperar 3 s de LLM), e o *tool calling* do modelo para
todo o resto. É exatamente o desenho que já começamos a ter em `jarvis_intencoes.py`.

---

## 2. Onde estamos contra isso

| Peça | Padrão "Jarvis 2026" | Este projeto | Distância |
|---|---|---|---|
| Wake word | openWakeWord local (~80 ms), sem transcrever | transcreve tudo e procura "jarvis" no texto | 🔴 média |
| VAD / fim de fala | silero-vad + endpointing | detector próprio adaptativo, testado | 🟢 **já temos, e bem feito** |
| Barge-in (falar por cima) | padrão | motor de fala aceita `!parar`, mas o laço pausa a escuta enquanto fala | 🟡 pequena |
| STT | faster-whisper local | Google → Whisper → Vosk → SAPI (cadeia pronta) | 🟡 falta provisionar o Whisper |
| **Roteamento** | **LLM com tool calling** | **intenções fixas + LLM só para conversa** | 🔴 **a maior** |
| Memória | embedding (ChromaDB/SQLite-vss) + extração automática de fatos | interseção de palavras nas últimas 2000 linhas | 🔴 grande |
| Voz (TTS) | Piper/Kokoro neural, local, natural | SAPI do Windows (robótica) em processo persistente | 🔴 grande |
| Proatividade | heartbeat/idle, briefings, lembretes | **não existe** | 🔴 grande |
| Persona | system prompt forte ("sir", seco, espirituoso) | 4 linhas de prompt | 🟡 pequena |
| Estados visuais / HUD | orbe + HUD (modo, CPU, mic, app em foco) | painel rico: câmera, chat, navegador, sistema | 🟡 falta a máquina de estados |
| Segurança | confirmação + audit trail + correlation id | confirmação em clique/comando parecido; log por processo | 🟡 média |
| MCP / plugins | registro de ferramentas externo (MCP) | nada | 🔵 opcional |
| Casa (Home Assistant) | ferramentas geradas a partir de `/api/states` | nada | 🔵 depende de você ter |
| **Visão de câmera** | **quase nenhum tem** | **rosto, expressões, gestos, holograma 3D, bloqueio por ausência** | ✅ **estamos na frente** |
| **Tela** | alguns leem tela com VLM | OCR + descrever tela com IA + **clicar em algo pelo nome** | ✅ **estamos na frente** |
| Navegador embutido | raro | navegador no painel com abas e automação de música | ✅ **estamos na frente** |

**Leitura honesta:** a *casca* da Jarvis (o que ela vê, o que ela mostra, o que ela
toca) já é mais rica que a da maioria dos Jarvis da internet. O que falta é o
**cérebro**: hoje ela é um excelente executador de comandos com um LLM pendurado
para conversar. O Jarvis do filme é o contrário — um agente que tem ferramentas.

---

## 3. Boa notícia sobre o cérebro

Não precisa trocar de stack. O que já está instalado suporta a virada:

* **Ollama + `qwen2.5` (todos os tamanhos, inclusive o `3b` que já usamos)
  suportam tool calling nativo** no endpoint `/api/chat` com o parâmetro `tools`.
* Regra prática dos guias: quanto menor o modelo, menos confiável a chamada de
  ferramenta. Com 8 GB de RAM dá para usar `qwen2.5:7b-instruct`; com 16 GB,
  `qwen3:8b` (melhor em ferramentas). `3b` funciona, mas pede schemas curtos e
  poucas ferramentas por chamada.
* **Piper tem vozes pt-BR** (`pt_BR-faber-medium`, `pt_BR-cadu`) e roda em CPU em
  tempo real; existe até uma voz pt-BR fine-tuned ("Razo", 15,6M parâmetros,
  RTF < 0,1). É a troca de voz mais barata e de maior impacto perceptível.
* **`nomic-embed-text`** (~270 MB) no Ollama faz os embeddings da memória;
  SQLite + FTS5 já resolve 80% sem depender de ChromaDB.

Ou seja: **a virada de "comandos" para "agente" é feita com o que você já tem.**
O que eu preciso escrever é o registro de ferramentas, o laço agêntico e a
camada de memória — e provisionar dois modelos (`ollama pull`) + uma voz Piper.

---

## 4. Caminho proposto

Cada fase entrega algo que você **ouve ou vê** no mesmo dia, e nenhuma fase
quebra o que já funciona (as intenções atuais viram o caminho rápido do
roteador híbrido, não são jogadas fora).

### Fase A — O cérebro vira agente *(a mudança estrutural)* — ✅ **feita em 16/10/2026**
* `jarvis_ferramentas.py`: registro com schema JSON gerado a partir das funções
  que já existem (`abrir_aplicativo`, `tocar_musica`, `pesquisar`,
  `descrever_tela`, `clicar_em`, `controlar_musica`, `gerar_imagem`, `hora`,
  `holograma`, `camera`, `esconder_painel`, `corrigir_texto`, …).
* Laço agêntico em `/api/chat` com `tools`, executando **todas** as chamadas da
  lista (não só a primeira — erro clássico), com teto de rodadas e timeout.
* Roteador híbrido: intenção com confiança ≥ 0,95 executa direto (instantâneo);
  o resto vai para o modelo decidir.
* `estado_voz` vira máquina de estados (`DORMINDO/OUVINDO/PENSANDO/FALANDO`)
  publicada no painel; audit trail com correlation id no log.
* **Como ficou:** `jarvis_ferramentas.py` (registro, 16 ferramentas, schema no
  formato do Ollama), `jarvis_agente.py` (laço, auditoria com correlation id,
  tetos de rodada/ferramenta e dois recuos: protocolo de texto para modelo sem
  `tools`, conversa antiga se o Ollama estiver fora), roteamento híbrido no
  `main.py` com detector de pedido encadeado, e 52 testes novos com modelo de
  mentira (nenhuma rede). *"abre o chrome e pesquisa o preço do ssd"* agora
  executa as duas ferramentas.
* **Pendente da fase:** a máquina de estados visual no painel (hoje o painel já
  recebe `processando` + motivo; os estados por nome ficaram para a Fase D, junto
  com o wake word).
* **Risco que continua:** modelo pequeno erra a chamada de vez em quando. Com
  `qwen2.5:3b` funciona; `qwen2.5:7b`/`qwen3:8b` acertam mais. O caminho
  determinístico segue lá para os comandos do dia a dia.

### Fase B — Memória de verdade
* SQLite: `mensagens`, `fatos` (com embedding), `sessoes`; migração do JSONL.
* `nomic-embed-text` para recuperar por sentido, não por palavra igual.
* **Extração de fatos automática**: ao fim de cada troca, o modelo responde se
  aprendeu algo sobre você ("trabalha com X", "gosta de Y", "mãe chama Z") e
  isso vira registro — é o que faz a Jarvis parecer que te conhece.
* Compactação de contexto por resumo (o histórico para de crescer no prompt).
* **Ganho:** *"lembra que eu falei daquele curso?"* funciona.

### Fase C — Voz e escuta de gente
* Piper pt-BR como motor principal, SAPI como reserva (o `jarvis_tts.py` já tem
  a arquitetura de processo persistente e `!parar` — troca-se o motor dentro).
* `faster-whisper` provisionado por script (como o `preparar_modelos.py` faz com
  a câmera) e promovido a motor preferido offline.
* **Barge-in de verdade**: enquanto ela fala, o ouvinte continua ativo; se você
  falar, corta a fala e atende.
* Persona no system prompt (tom, "senhor", humor seco, respostas curtas).
* **Ganho:** ela para de soar como o Windows 98 e passa a te ouvir no meio.

### Fase D — Acordar de verdade
* openWakeWord (ou Vosk em modo palavra-chave) escutando "Jarvis" sem transcrever
  tudo; transcrição só depois do wake.
* Estados visuais no painel: orbe/anel reagindo a `OUVINDO/PENSANDO/FALANDO`
  (o `NucleoJarvis` que já desenha no `paintEvent` é o lugar certo).
* Push-to-talk por hotkey global como alternativa.
* **Ganho:** custo de CPU cai, falso "Sim?" desaparece, e a interface comunica.

### Fase E — Proatividade (o que mais parece o filme)
* `heartbeat` no processo de voz: regras + contexto (hora, app em foco, tempo
  sem rosto, expressão, última conversa).
* Briefing da manhã, lembretes ("me lembra de X às 18h" → ferramenta
  `criar_lembrete`), e comentários curtos quando faz sentido — com **orçamento
  de interrupção** (máx. N por hora, nunca enquanto você digita).
* A câmera que já existe vira insumo: *"você está há 90 minutos sem levantar"*.
* **Ganho:** ela começa a falar primeiro. É aqui que vira "Jarvis".

### Fase F — Alcance (opcional, depende do que você usa)
* Home Assistant: ler `/api/states` e **gerar as ferramentas dinamicamente**
  (luzes, tomadas, sensores) — sem escrever uma função por dispositivo.
* Gerador de imagem local (ComfyUI/A1111) ligado na ferramenta `gerar_imagem`,
  que hoje só abre uma busca e avisa que não gera.
* MCP: expor as ferramentas para outros clientes / aceitar servidores externos.
* Spotify API real (em vez de dirigir o YouTube Music pelo navegador).

---

## 5. Decisões tomadas (16/10/2026)

1. **Ordem:** Fase A escolhida e **concluída**. Próxima a decidir: B (memória),
   C (voz), D (wake word + estados) ou E (proatividade).
2. **Celular:** a Jarvis deve, um dia, rodar também no telefone — e isso muda o
   desenho: **o cérebro (modelo, ferramentas, memória) fica separado de quem
   executa**. Por isso a Fase A já nasceu assim: o registro de ferramentas é
   dado puro (`registro.como_json()`), a execução recebe a ferramenta por nome e
   argumentos, e o agente só conhece uma URL de `/api/chat`. No celular (Android
   via Termux, ou um cliente fino que fala com o PC pela rede) isso é o mesmo
   contrato, sem reescrever a lógica. Nada de LangChain/ChromaDB: biblioteca
   padrão sempre que possível, porque peso é o que decide o que roda no bolso.
3. **Local ou nuvem:** ainda em aberto — e a resposta certa depende do item 2.
   A arquitetura já permite os três: local (`127.0.0.1:11434`), servidor na
   rede, ou endpoint na nuvem, tudo trocando uma URL no `config_jarvis.json`.
4. **Hardware:** ainda não sei (você não tinha certeza). Comando para descobrir
   no Windows:

   ```powershell
   Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM
   Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory
   ```

   Com isso eu digo se vale `qwen2.5:3b`, `qwen2.5:7b` ou `qwen3:8b`.
5. **Casa/integrações (Fase F):** depende de você ter Home Assistant, lâmpada
   inteligente ou Spotify premium — decide depois.

---

## Fontes consultadas (12/10/2026)

* `im4peace/Jarvis` e `serj009/Jarvis` — Windows 100% local: wake word,
  silero-vad, faster-whisper, Piper, Ollama com tool calling, barge-in,
  roteador híbrido, intents compostos, registry MCP, correlation id,
  config versionado com migração.
* `rezaulhreza/jarvis` — local-first com Ollama: 35+ ferramentas, memória em
  SQLite, RAG com ChromaDB e reranking, extração de fatos, compactação de
  contexto por resumo, geração de imagem/vídeo/música.
* `vaibhav07772/jarvis` — pipeline didático wake → STT → memória → LLM →
  ferramentas → TTS, com ChromaDB e embeddings MiniLM.
* `miriho6643/Project-Jarvis` — roteador de modelos por tarefa, fallback por
  latência, ferramentas geradas dinamicamente do Home Assistant.
* `shahzaibali0710/jarvis-voice-assistant` — wake word + faster-whisper +
  tool calling + edge-tts, hotkey global, UI web, e uma seção honesta de
  "o que sai da sua máquina".
* `llm-guy/jarvis` — o caso mais próximo do nosso: Qwen via Ollama + wake word
  + tool calling com LangChain.
* `Rafay-12-AE/Jarvis-V2.0` — arquitetura `brain/ core/ skills/ ui/` e máquina
  de estados SLEEPING/LISTENING/THINKING/SPEAKING na interface.
* `project-jarvis` (PyPI) — orquestração hierárquica (ROOT diálogos/memória,
  DISPATCH ferramentas) e descoberta de ferramentas por embedding.
* Guias: *insiderllm.com — Building a Local AI Assistant* (tabela de VRAM por
  componente e níveis 1-5), *openvoxai — Build a Local Jarvis* ("comece pelo
  laço menor; nunca dê execução irrestrita a um agente experimental"),
  *betterclaw / theneuralbase* (quais modelos do Ollama suportam tool calling:
  `qwen2.5` em todos os tamanhos, sim; `llama3`, `gemma3`, `phi4`, `qwen2.5vl`,
  não).
* Voz pt-BR: `OHF-Voice/piper1-gpl` (lista de idiomas inclui pt_BR),
  `pt_BR-faber-medium`, HaikuTTS (`pt_BR-cadu`), `Lucasllfs/Razo-piper-voice`.
