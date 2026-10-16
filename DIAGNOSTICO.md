# Diagnóstico técnico — J.A.R.V.I.S

**Data:** 11/10/2026
**Escopo:** `main.py` (voz/assistente), `jarvis_tela.py` (interface/câmera), `iniciar_jarvis.py`, arquivos JSON de estado
**Método:** leitura integral dos dois arquivos (3.186 linhas), análise estática (pyflakes/vulture), execução de testes de comportamento com mediapipe 0.10.14 e simulação numérica dos algoritmos de áudio, e mineração dos 1.098 registros do seu `memoria_jarvis.json`.

**Legenda das evidências**
✅ = provado executando código   🔍 = por leitura de código (arquivo:linha)   📊 = pelos seus próprios dados de uso

---

## Resumo executivo

| # | Problema | Impacto |
|---|---|---|
| B1 | **Nenhum log em arquivo** — todo erro é engolido por `except Exception` e virou frase genérica falada | Impossível diagnosticar "às vezes dá problema" |
| B2 | **Reconhecimento de voz sem plano B** — depende do Google pela internet | 📊 **82 falhas**, sendo **81 num único dia** (21/08). Nesses momentos a Jarvis fica surda e repete o erro em loop |
| B3 | **Comando digitado no painel pode levar até 60 s** para ser processado | O loop de voz bloqueia a única thread por até `TEMPO_MAXIMO_DE_ESPERA=60` |
| B4 | **Fechar a janela mata a câmera** (e pode matar o app) | ✅ Erro a cada 220 ms, para sempre — reproduzido em laboratório |
| B5 | **Corrida nos JSONs entre os dois processos** — comando/mensagem pode sumir | "falei e ela não fez nada" |
| B6 | **Memória cresce sem limite e é reescrita inteira a cada frase** | Degradação progressiva |
| B7 | **Bloqueio automático do PC ligado por padrão** | ✅ O computador pode travar sozinho após 8 s sem rosto |
| B8 | **Limiar de voz nunca se adapta ao ruído do ambiente** | ✅ Simulado: em ambiente barulhento **nunca mais escuta**; em ambiente silencioso dispara sozinho 📊 105× "Sim?" |
| B9 | **Microfone pode ser escolhido errado** (não checa canal de entrada) | "abre e nunca escuta" |
| B10 | **TTS abre um processo PowerShell por frase**, sem interrupção | Resposta lenta; não dá para falar por cima |
| B11 | **Comandos casados por substring não entendem seu jeito de falar** | 📊 11+ vezes ela respondeu "você precisa usar um comando específico" |
| B12 | **Sem `sys.excepthook`** | Qualquer erro em slot/thread morre invisível |

**Duas funcionalidades existem no código mas são impossíveis de alcançar:**
- **Holograma nunca liga** — nenhum trecho do projeto emite o evento `mostrar_holograma`. As respostas *"Holograma ativado."* no seu histórico 📊 (4×, em 19/08) foram **alucinação do LLM**, não o app fazendo algo.
- **Botão "Gerar imagem"** envia `gerar imagem ...` para o `main.py`, que **não tem nenhum tratamento** para esse texto.

---

## Provas de laboratório (o que eu executei)

**1. `detect()` depois de `close()` derruba o processamento da câmera** ✅

```
FaceDetector criado com modelo real → detect() OK → close() OK
detect() após close → ValueError: Task runner is currently not running.
```
E é exatamente o que o app faz hoje: `closeEvent` chama `liberar_camera()` (que fecha os 3 detectores) e **ignora** o fechamento da janela, mas o timer da câmera continua disparando a cada 220 ms.
`jarvis_tela.py:1804-1819` + `jarvis_tela.py:420`

**2. O limiar de ruído é matematicamente incapaz de se adaptar** ✅

Simulei a fórmula de `main.py:1064-1070` (`min(nr*1.08 + nivel*0.12, max(nivel, nr))`, partindo de `nivel_ruido = 120/1.8 = 66,7`, apenas nos primeiros 1,0 s de cada escuta):

| Ruído de fundo (RMS) | Limiar resultante | Consequência |
|---|---|---|
| 5 a 66 | **120 (sempre o mínimo)** | Teclado/mouse/ventilador (~150-300) **dispara falso positivo** |
| 300 | 540 | funciona, no limite |
| 800 (ventilador/AC perto do mic) | **1.440** | voz normal (~600-1500) **nunca passa** → a Jarvis não te escuta mais, e não se recupera |
| 2000 | 3.360 | idem, pior |

O valor só pode **subir** e só nos primeiros instantes de cada escuta — nunca acompanha o ambiente ao longo do tempo.

**3. Modelos do mediapipe e o que dá para resolver offline** ✅

| Arquivo exigido em `jarvis_tela.py:201,210,220` | Existe no wheel do mediapipe? | Situação |
|---|---|---|
| `blaze_face_short_range.tflite` | ❌ com esse nome, mas ✅ **o mesmo modelo existe** como `modules/face_detection/face_detection_short_range.tflite` e **funciona** com o `FaceDetector` — dá para copiar sem internet |
| `face_landmarker.task` | ❌ ausente | precisa baixar (script de provisionamento) |
| `hand_landmarker.task` | ❌ ausente | precisa baixar (script de provisionamento) |

Nenhum dos 3 está no repositório, não estão no `.gitignore` e nada os baixa: **um clone novo faz a interface morrer no startup**.

Bônus: a **API legada** (`mp.solutions.face_mesh`, `mp.solutions.hands`, `mp.solutions.face_detection`) roda 100% offline com os `.tflite` que já vêm no pacote. Serve de *fallback* completo (21 pontos/mão e 468 pontos/rosto), com a ressalva de que ela **não fornece blendshapes** — a estimativa de emoções precisaria passar a usar geometria.

**4. APIs que o app usa e que eu confirmei existirem** ✅
`vision.FaceLandmarksConnections.FACE_LANDMARKS_CONTOURS` (124 conexões) existe; `RunningMode.IMAGE` ok — essas partes estão corretas.

**5. Custo da inferência na thread da interface** ✅
Só o `hands` roda ~22 ms/frame nesta máquina; com face detector + face landmarker + hands dentro do timer de 220 ms, **toda a análise de câmera acontece na thread da GUI** (`jarvis_tela.py:420,953`) — é a causa provável de engasgos ao digitar, rolar e enquanto uma página carrega no navegador embutido.

**O que eu NÃO consegui testar daqui** (Linux, sem Windows, sem áudio, sem câmera): PowerShell/SAPI, `keybd_event`, `SendKeys`, `rundll32 LockWorkStation`, latência real do TTS, listagem real de microfones, driver da webcam (`cv2.CAP_DSHOW`).

---

## Detalhamento por bug

### B1 · 🔴 Sem log em arquivo — todo erro vira frase genérica 🔍
Há `except Exception` genérico em pelo menos 10 pontos (`main.py:503, 563, 670, 727, 797, 930, 1010, 1130, 1355`; `jarvis_tela.py:822`), sempre terminando em `falar("Não consegui...")`. Nada é gravado em disco, nenhum `traceback` é registrado. Você fica sem qualquer rastro do que falhou.
**Correção:** `logging` para `jarvis.log` (rotativo) + `sys.excepthook` + `logging.exception` em cada `except`.

### B2 · 🔴 Reconhecimento de voz sem plano B 🔍📊
`transformar_audio_em_texto` (`main.py:1119`) usa `recognize_google`, que depende de internet + endpoint gratuito do Google. No `except sr.RequestError` (`main.py:1352`) o app só fala *"Erro ao acessar o reconhecimento de voz."* e tenta de novo — para sempre.
📊 Seu histórico mostra **81 ocorrências num único dia (21/08/2026)** e mais 1 em 22/08. Ou seja: houve um episódio longo (bloqueio/rate-limit/sem rede) em que a Jarvis ficou inutilizável e ainda gastou sua memória com o mesmo texto 81 vezes.
**Correção:** cadeia de fallback (Windows SAPI offline via `System.Speech`, ou Vosk pt-BR, ou `faster-whisper` local), troca automática para o motor offline após N falhas seguidas, aviso de erro **uma vez** (e no painel, não falado a cada tentativa), e teste de conectividade.

### B3 · 🔴 Comando digitado no painel espera até 60 s 🔍
`main.py:52` (`TEMPO_MAXIMO_DE_ESPERA = 60`), `main.py:1018` (`ouvir_ate_silencio`) e `main.py:1270` (comandos da interface só são lidos no topo do laço). Como `ouvir_ate_silencio` bloqueia a única thread, um texto enviado no chat só é processado quando a escuta retorna — **até 60 segundos depois**. É também por isso que a sua mensagem só aparece no histórico depois, e não na hora.
**Correção:** captura de áudio em thread separada + fila única de comandos; o laço principal vira um consumidor de eventos (isso também habilita interromper a fala da Jarvis).

### B4 · 🔴 Fechar a janela quebra a câmera (e pode derrubar o app) ✅🔍
`jarvis_tela.py:1804-1819`: o `closeEvent` faz `evento.ignore()` (a janela não fecha, só some), chama `liberar_camera()` — que fecha os 3 detectores — e esconde. Mas o `QTimer` da câmera (`jarvis_tela.py:420`, 220 ms) **continua rodando** e chama `detect()` em detectores fechados → ✅ `ValueError: Task runner is currently not running.` a cada 220 ms, para sempre. Não existe `sys.excepthook`: dependendo da build de Qt, exceção não tratada em slot aborta o processo (comportamento documentado de `qFatal`), ou então você fica com um festival de tracebacks e a câmera congelada. No encerramento, `aboutToQuit` (`jarvis_tela.py:1825`) chama `liberar_camera()` de novo → `close()` duplo.
**Correção:** separar "esconder" de "encerrar"; fechar os detectores em um único ponto, com flag de guarda; `try/except` no corpo do timer; `sys.excepthook` global.

### B5 · 🔴 Corrida nos arquivos JSON entre os dois processos 🔍
Dois processos fazem *read-modify-write* nos mesmos arquivos, sem trava e sem escrita atômica (`Path.write_text` trunca o arquivo primeiro):
- `comandos_interface.json`: a GUI anexa (`jarvis_tela.py:1628`) enquanto o `main.py` lê e regrava `"[]"` (`main.py:201-225`) → **o comando pode desaparecer**;
- `eventos_interface.json`: mesma coisa (`main.py:227-244` vs `jarvis_tela.py:1119-1148`) → **o navegador pode não abrir**;
- `memoria_jarvis.json`: o `main.py` regrava tudo a cada frase (`main.py:188-199`) enquanto a GUI lê a cada 1 s (`jarvis_tela.py:912, 1579`) → leitura parcial → `JSONDecodeError` → o chat pisca *"Ainda não há mensagens"* e a memória pode perder registros.

**Correção:** escrita atômica (`tmp` + `os.replace`), e uma regra de escritor único — no lugar dos dois arquivos de fila, usar um canal local (socket/HTTP em `127.0.0.1`) ou arquivos individuais por mensagem (padrão "spool"), que não têm corrida.

### B6 · 🟠 Memória sem teto, reescrita inteira a cada mensagem 🔍📊
📊 Hoje: 1.098 itens, ~148 KB — e cada fala/registro reescreve o arquivo inteiro. O painel analisa o arquivo **duas vezes por segundo** (contagem de comandos + histórico). Isso cresce linearmente e vai engasgando a interface.
**Correção:** `memória.jsonl` em modo *append* (ou SQLite), teto/rotação por tamanho, leitura com cache por `mtime`.

### B7 · 🟠 Bloqueio automático do PC ligado por padrão 🔍
Com `bloquear_ao_sair: true`, se o rosto não for detectado por 8 s o app executa `rundll32 user32.dll,LockWorkStation` (`jarvis_tela.py:1060-1077`). Se a câmera falhar, não entregar frames ou você só estiver de lado, `self.ultimo_rosto` fica velho → **o PC trava "sozinho"**. Risco real de perder trabalho em andamento.
**Correção:** padrão `false`; exigir câmera saudável (leituras recentes com sucesso), tempo mínimo de sessão e contagem regressiva avisada antes de bloquear.

### B8 · 🟠 Detector de voz: ver prova 2 acima 🔍✅
Além do limiar, dois detalhes de projeto: a calibração só roda no primeiro 1,0 s de cada chamada (e reinicia do zero toda vez), e não há exigência de relação sinal/ruído persistente. 📊 Resultado no seu histórico: 105 entradas "Sim?" (a Jarvis acordando sem comando) — 52 só em 16/08.
**Correção:** piso de ruído adaptativo contínuo (mediana/percentil dos últimos N blocos, com piso e teto), exigência de SNR, *hangover* e pré-buffer ajustados.

### B9 · 🟠 Microfone pode ser escolhido errado 🔍
`selecionar_dispositivo_microfone` (`main.py:977`) casa apenas pelo nome e **nunca verifica `max_input_channels > 0`**. No Windows o mesmo dispositivo WASAPI aparece como entrada e saída com o mesmo nome — então um dispositivo só de saída com "mic"/"array" no nome pode ser escolhido e o `InputStream` falha sempre. O erro aparece só no console (que você não vê se abrir pelo atalho) e a Jarvis nunca ouve.
**Correção:** filtrar por canal de entrada, preferir o dispositivo padrão de entrada, guardar a escolha e expor no painel; mostrar erros de áudio no painel.

### B10 · 🟠 TTS lento e não interrompível 🔍
Cada frase abre um processo PowerShell novo (`main.py:309-336`) — custo típico de centenas de ms a mais de 1 s por fala — e, enquanto fala, o laço fica proibido de escutar (`main.py:1285-1288`).
**Correção:** processo de TTS persistente (SAPI com pipe, `pyttsx3` ou `edge-tts`) + fila + interrupção (*barge-in*) quando você começa a falar.

### B11 · 🟠 Comandos não entendem seu vocabulário 🔍📊
O roteamento é `startswith`/`in` sobre strings fixas (`main.py:1157-1267`). 📊 Seu histórico tem "ta vendo minha tela?", "o que estou vendo", "descreva o que está na minha tela", "para ligar a câmera", "esconder o painel", "tira holograma" — todas caíram no LLM, que respondeu *"você precisa usar um comando específico da Jarvis"* (11+ vezes). Pior: o LLM **afirma ter executado ações** (*"Holograma ativado."*, *"Abrindo o YouTube..."*, 4× em 19/08) mesmo com instrução em contrário no prompt — modelo 3B não obedece isso de forma confiável.
**Correção:** camada de intenções (sinônimos + pontuação fuzzy + confirmação "quis dizer...?"), e rotear ações suportadas **antes** de chamar o LLM; o LLM fica só com conversa.

### B12 · 🟠 Sem `sys.excepthook` 🔍
Qualquer erro fora dos `try` só imprime traceback no console — que some quando você abre pelo atalho. Do ponto de vista do usuário, "a Jarvis parou de fazer X" sem explicação.
**Correção:** excepthook → log + notificação na bandeja.

### B13 · 🟡 `iniciar_jarvis.py` deixa processo órfão 🔍
Se a interface morrer (ex.: modelo faltando), o processo de voz continua rodando escondido: **você ouve a Jarvis, mas nenhuma janela aparece** — e o mutex impede abrir de novo. Também é Windows-only (`.venv\Scripts\python.exe`, `ctypes.windll`).
**Correção:** verificar os pré-requisitos antes de abrir, mostrar erro visível, encerrar o irmão em qualquer saída (não só no `finally` do subprocess), suportar Linux/macOS.

### B14 · 🟡 Modelo do mediapipe ausente do projeto (ver prova 3) 🔍✅
**Correção:** `preparar_modelos.py` que (a) copia `face_detection_short_range.tflite` do próprio pacote mediapipe → `blaze_face_short_range.tflite`, (b) baixa os dois `.task` se faltarem, (c) valida por tamanho/hash, (d) imprime erro claro. E um fallback para a API legada quando os `.task` não existirem.

### B15 · 🟡 Análise de câmera na thread da interface (ver prova 5) 🔍✅
**Correção:** worker (`QThread`) com fila de 2-3 frames; a GUI só recebe o resultado (rastros/gesto/emoção) e o frame anotado.

### B16 · 🟡 Configuração com chaves mortas 🔍
`visao_ativada` e `bloquear_com_punho` estão no `config_jarvis.json` e **não são lidas em lugar nenhum**; o diálogo de Configurações não cobre microfone, voz/modelo e o comportamento do bloqueio; o texto diz que as opções "valem para a próxima resposta", mas câmera/gesto/holograma aplicam na hora.
**Correção:** dicionário de configuração com chaves vivas + validação e o diálogo completo.

### B17 · 🟡 `SendKeys` vai para a janela em foco 🔍
Gestos (rolar/voltar/play) usam `SendKeys` (`jarvis_tela.py:1465`) e agem sobre **a janela que estiver em foco** — um gesto pode virar PageDown num app sem relação — e ainda abrem um PowerShell por evento. "Aumentar volume" manda **um** toque em `VK_VOLUME_UP` (+2%): quase não muda nada.
**Correção:** `SendInput`/scan codes com repetição (e `use_last_error=True`), ou volume por sessão via WASAPI; limitar gestos ao painel da Jarvis.

### B18 · 🔵 Recursão de comando aprendido sem limite 🔍
`main.py:1172`: `return processar_comando(acao_aprendida)`. Par de comandos A→B e B→A gera `RecursionError` (capturado pelo `except` genérico do laço como "Erro no loop de áudio"). Cada comando aprendido também registra memória de novo.
**Correção:** limite de profundidade + detecção de ciclo.

### B19 · 🔵 Retry do Ollama é código morto e o caminho é fixo 🔍
`main.py:639-668`: se o `urlopen` falhar, o `except` chama `iniciar_ollama_se_necessario()`, que **levanta `RuntimeError` quando o executável não está no caminho fixo** (`main.py:72-78`, `C:\Users\Matheus\...`) → a exceção escapa e a segunda tentativa nunca acontece; você recebe "Não consegui acessar a IA local" sem chance de recuperação. Não há `shutil.which("ollama")`. Pior: `timeout=120` em duas tentativas pode **congelar o laço por ~4 minutos** sem nenhum aviso no painel.
**Correção:** `shutil.which`, retry real, checagem de saúde, timeout menor, estado "pensando" no painel.

### B20 · 🔵 Código e dependências mortos 🔍
`abrir_automacao_youtube_music` + imports do selenium (`main.py:29-33, 840`) — por isso `selenium` está no `requirements.txt`; `imagem_para_base64` + `BytesIO` (`main.py:461`); `carregar_historico` (`jarvis_tela.py:85`); `palma_virada_para_cima` (`1229`); `atualizar_controle_holograma` (`1238`, substituída pela versão de duas mãos — **controle por pinça com uma mão não existe**); `processar_pagina_navegador` (`859`); `self.layout_navegador` (`635`); `movimento` (`983`); `dimensoes_frame` (`1238`); imports não usados; `teste_voz.py` (pyttsx3, abordagem abandonada).

---

## Funcionalidades que existem no código mas são inalcançáveis

| Recurso | Situação | Caminho |
|---|---|---|
| **Holograma 3D** (`desenhar_holograma_67`, `malha_3d_67`, `projetar_ponto_3d`) | Nunca executa: `holograma_visivel` só vira `True` ao ler o evento `mostrar_holograma` (`jarvis_tela.py:1142`) — e **nenhum trecho do projeto emite esse evento** (só existe `navegar_navegador`, `main.py:248`) | criar o comando de voz + gatilho no painel/gesto que emite o evento; ou remover o código |
| **"Gerar imagem"** (botão em `jarvis_tela.py:1618`) | Envia `gerar imagem ...`; `main.py` **não tem tratamento** para esse texto → cai no LLM, que não gera imagem | implementar (ex.: ComfyUI/A1111 local ou API) ou esconder o botão |
| **Controle do holograma com uma mão** | `atualizar_controle_holograma` é código morto; só a versão de duas mãos é chamada | religar ou remover |
| `visao_ativada`, `bloquear_com_punho` | Chaves de config que nada lê | remover ou implementar |

---

## Roteiro sugerido

| Fase | Conteúdo | Bugs | Esforço |
|---|---|---|---|
| **1. Observabilidade e não-travar** | log em arquivo + excepthook; corrigir `closeEvent`/`liberar_camera` + guarda no timer; bloqueio do PC `false` por padrão com contagem; `iniciar_jarvis` que falha de forma visível | B1, B4, B7, B12, B13 | ~2 h |
| **2. "Não me escuta / não responde"** | fila de comandos não bloqueante (áudio em thread); limiar de ruído adaptativo real; seleção correta de microfone + escolha no painel; fallback offline de voz; erro de voz mostrado no painel (não falado em loop) | B2, B3, B8, B9 | ~4 h |
| **3. Dados à prova de corrida** | escrita atômica + `memória.jsonl`/SQLite; fila de comandos sem corrida; cache por `mtime`; teto/rotação | B5, B6 | ~2 h |
| **4. Entender o que você fala** | camada de intenções (sinônimos + fuzzy + confirmação); LLM deixa de responder por ação suportada; testes unitários da camada (rodam sem Windows) | B11, B19 | ~3 h |
| **5. Recursos que você já pediu** | holograma de verdade (comando + evento + gesto); gerar imagem (ou remover); TTS persistente + interrupção; câmera em thread separada | FG1, FG2, B10, B15 | variável |
| **6. Limpeza e portabilidade** | remover código/deps mortos; provisionar modelos; separar núcleo multiplataforma do específico Windows; testes | B14, B16, B17, B18, B20 | ~3 h |

**Ordem recomendada:** 1 → 2 → 4 → 3 → 5/6 (o ganho percebido está nas fases 1 e 2; a fase 4 resolve a irritação do "use um comando específico").

---

## Checklist para rodar no seu PC (coisas que só você pode confirmar)

```powershell
# 1. Veja o erro real: rode no console, não pelo atalho
cd <pasta do jarvis>
python jarvis_tela.py

# 2. Os 3 modelos estão na pasta?
dir *.task, *.tflite

# 3. O Ollama está onde o código espera?
where.exe ollama
Test-NetConnection 127.0.0.1 -Port 11434

# 4. Qual microfone o app está escolhendo? (dentro do venv)
python -c "import sounddevice as sd; [print(i, d['name'], 'IN=', d['max_input_channels'], 'OUT=', d['max_output_channels']) for i, d in enumerate(sd.query_devices())]"

# 5. Versão instalada do PySide6 e do mediapipe
python -c "import PySide6, mediapipe; print(PySide6.__version__, mediapipe.__version__)"
```

Itens 2, 3 e 4 são os que mais provavelmente explicam travamentos silenciosos na sua máquina.

---

# Status da implementação (11/10/2026)

Tudo o que está marcado como ✅ foi implementado e tem teste automatizado.
Os testes rodam **sem Windows** (158 testes, `python -m unittest discover -s testes -t .`):

```
Ran 158 tests — OK (skipped=1: fumaça da interface, precisa de PySide6 + OpenGL)
```

## Fase 1 — Observabilidade e não-travar

| Bug | Situação | Como |
|---|---|---|
| B1 (sem log) | ✅ | `jarvis_comum.configurar_log()` grava log rotativo (2 MB × 3) **um arquivo por processo** — `jarvis.log` (voz) e `jarvis.tela.log` (interface); **todos** os `except` genéricos passaram a registrar `log.exception` antes de falar |
| B4 (fechar janela mata a câmera) | ✅ | o **X só esconde** (não existe mais `close()` de detetor em uso); `detect()` roda dentro de `try` no timer; `liberar_camera()` é chamado uma única vez, com flag. Teste de bancada: `detect()` após `close()` → `ValueError: Task runner is currently not running.` — reproduzido e explicado |
| B7 (bloqueio do PC ligado por padrão) | ✅ | padrão agora é `false`; mesmo ligado: exige câmera saudável, 1 min de sessão e **avisa 10 s antes** |
| B12 (sem excepthook) | ✅ | `registrar_excecoes_nao_tratadas()` nos dois processos — log + aviso na tela (com limite de frequência) |
| B13 (processo órfão) | ✅ | `iniciar_jarvis.py` reescrito: confere dependências/modelos antes, vigia os dois processos e encerra o irmão em qualquer saída; funciona fora do Windows |

## Fase 2 — "Não me escuta / não responde"

| Bug | Situação | Como |
|---|---|---|
| B2 (voz sem plano B) | ✅ | `jarvis_voz.py`: cadeia **Google → Whisper local → Vosk → SAPI do Windows**; após 3 falhas seguidas entra em modo offline por 5 min; erro é **avisado uma vez** (no painel e falado), não em laço |
| B3 (comando do painel espera 60 s) | ✅ | captura de áudio em **thread própria** (`jarvis_audio.ThreadOuvinte`); o laço principal nunca mais fica surdo ao painel; espera máxima por resposta caiu para 8 s |
| B8 (limiar que nunca se adapta) | ✅ | piso de ruído adaptativo com mediana/piso/teto + **vigia de ruído constante**: se o sinal fica acima do limiar sem parar e sem modular (ventilador, motor), o piso é recalibrado — sem isso o app ficava "surdo" para sempre. Tem teste com ventilador sintético a 120 Hz |
| B9 (microfone errado) | ✅ | `escolher_microfone()` filtra por canal de entrada, prefere o padrão do sistema e aceita a escolha do painel (novo campo nas Configurações) |

## Fase 4 — Entender o que você fala

| Bug | Situação | Como |
|---|---|---|
| B11 ("use um comando específico") | ✅ | `jarvis_intencoes.py` (23 testes): sinônimos de verbo (abra/abre/inicia/executa/roda → abrir), palavras de enchimento, parecença com **faixa de confirmação** ("Você quis dizer...?") e as frases do seu histórico que falhavam: *"ta vendo minha tela"*, *"o que estou vendo"*, *"para ligar a câmera"*, *"tira holograma para mim"*, *"esconder painel"* |
| B19 (retry do Ollama morto + caminho fixo) | ✅ | `localizar_ollama()` (OLLAMA_EXECUTABLE → PATH → pastas usuais); retry de verdade com o erro real registrado; tempo limite de 60 s; painel mostra "Pensando..." |

## Fase 3 — Dados à prova de corrida

| Bug | Situação | Como |
|---|---|---|
| B5 (corrida nos JSON) | ✅ | `jarvis_comum.FilaDeArquivos`: cada comando/evento é **um arquivo próprio** (nada de ler-gravar-apagar o mesmo arquivo); escrita atômica em todos os JSON |
| B6 (memória reescrita inteira) | ✅ | histórico passou a `memoria_jarvis.jsonl` (uma linha por mensagem, anexo com rotação em 8 MB); o `.json` antigo é migrado na primeira execução e fica de cópia; painel lê com cache por `mtime` |

## Fase 5 — Recursos que você já pediu

| Item | Situação | Como |
|---|---|---|
| Holograma (nunca ligava) | ✅ | agora existe comando de voz de verdade: *"liga o holograma"*, *"tira o holograma para mim"*, *"quero um holograma"* → evento `mostrar_holograma` |
| Botão "Gerar imagem" (não fazia nada) | ✅ | o pedido é reconhecido e **respondido com honestidade**: "ainda não sei gerar imagens" + abre uma busca de imagens (em vez de cair no LLM e sumir) |
| B10 (TTS lento / não interrompível) | ✅ | `jarvis_tts.py`: **um** processo de fala para toda a sessão, com marcas de "falando/livre" e comando `!parar` (fim do PowerShell por frase). Se falhar, cai no modo antigo |
| B15 (câmera na thread da GUI) | 🟡 | **mitigado**: o modelo de expressões (o mais caro) roda a cada 3 quadros e o resultado é reaproveitado nos intermediários; o de mãos continua a cada quadro para o gesto responder. Thread separada fica como próximo passo |

## Fase 6 — Limpeza e portabilidade

| Bug | Situação | Como |
|---|---|---|
| B14 (modelos ausentes) | ✅ | `preparar_modelos.py`: copia o modelo de rosto **de dentro do pacote mediapipe** (sem internet) e baixa os dois `.task`; a interface avisa na tela o que falta e continua funcionando sem eles |
| B16 (chaves de config mortas) | ✅ | `visao_ativada` e `bloquear_com_punho` (que nada liam) saíram do `config_jarvis.json`; o diálogo de Configurações ganhou microfone e motor de voz |
| B17 (SendKeys na janela em foco) | ✅ | gestos usam `keybd_event` direto (sem abrir PowerShell por gesto) e o volume dá 5 passos por comando (antes 2% por comando) |
| B18 (recursão de comando aprendido) | ✅ | profundidade máxima + mensagem "os comandos aprendidos estão em ciclo" |
| B20 (código e dependências mortos) | ✅ | saíram: `selenium`, `pyttsx3`, `teste_voz.py`, `imagem_para_base64`, `abrir_automacao_youtube_music`, `carregar_historico`, `palma_virada_para_cima`, `atualizar_controle_holograma`, `processar_pagina_navegador`, `detectar_movimento` (rodava OpenCV a cada quadro para nada) e imports sem uso |

## Testes novos (rodam no Linux **e** no seu Windows)

| Arquivo | O que cobre |
|---|---|
| `testes/teste_audio.py` | detecção de voz: limiar adaptativo, vigia de ruído constante, microfone com entrada/saída |
| `testes/teste_voz.py` | cadeia de motores, modo offline, aviso de erro uma vez por minuto |
| `testes/teste_intencoes.py` | sinônimos, enchimento, confirmação e **ausência de falso positivo** na conversa livre |
| `testes/teste_comandos.py` | importa o `main.py` de verdade (com dublês de Windows) e roda 23 comandos do seu histórico: nenhum pode estourar |
| `testes/teste_fala.py` | protocolo do TTS persistente (base64, `!parar`, `!sair`, marcas de estado) |
| `testes/teste_tela.py` | fumaça da interface: monta a janela inteira com o navegador dublê (pula se não houver PySide6) |

### Ajuste de 12/10/2026 — primeiro aviso de falha de voz era engolido

`jarvis_voz.py`: `GerenciadorDeVoz.ultimo_aviso` nascia com `0.0` e era
comparado com `time.monotonic()`. Nos primeiros `INTERVALO_MINIMO_ENTRE_AVISOS`
(60 s) de vida da máquina a diferença fica menor que o intervalo, então o
**primeiro** aviso de erro de voz não aparecia — justamente o caso de quem liga
o PC e abre o Jarvis na sequência. Agora o campo nasce `None` e a comparação só
acontece quando não é `None`
(`testes/teste_voz.py::test_nao_repete_aviso_em_loop` cobre o caso).

## Rodada de 12/10/2026 — o que os testes novos pegaram

Depois do diagnóstico, uma passada de verificação **executando** o código (em
Linux, sem Windows/microfone/câmera) achou seis defeitos que nenhum dos 73
testes cobria. Todos corrigidos, com teste:

| # | Defeito | Efeito prático | Correção |
|---|---|---|---|
| C1 | O argumento da intenção saía da frase **sem enchimento** e era limpo em `main.py` com `str.replace` | *"gerar imagem de um dragão"* buscava **"imagem dragao"**; *"toca toque de midas no youtube"* buscava **"de midas"** (o "toque" do verbo era removido de dentro do título); *"abre o chrome para mim"* virava **"chrome pausar"** (canonicalizar troca "mim" por "pausar") | `jarvis_intencoes.extrair_argumento()` + `_argumento_do_verbo()`: limpeza **por palavra inteira**, com dicionário de vocabulário por intenção (`PALAVRAS_FORA_DO_ARGUMENTO`), miolo preservado ("toque **de** midas", "bloco **de** notas") e corte repetido da cauda de plataforma ("youtube music" sai inteiro). O argumento passa a vir da frase normalizada, não da canônica nem da versão sem enchimento |
| C2 | Pedido sem o essencial caía na faixa de confirmação | *"gerar imagem"* perguntava "Você quis dizer: gerar uma imagem?" — que não diz nada | `main.argumento_que_falta()`: quando falta o argumento, o Jarvis pergunta o que falta ("Diga o que você quer na imagem") em vez de confirmar o vazio |
| C3 | `listar_aplicativos()` rodava `Get-StartApps` **a cada** "abrir X" | o laço principal travava por segundos no comando mais usado do histórico | cache de 10 min (`CACHE_DE_APLICATIVOS`); lista vazia (falha) é tentada de novo em 30 s, não fica 10 min guardada |
| C4 | Nome parecido abria o programa **sem perguntar** (`difflib` cutoff 0,78) | "abrir chrome" podia abrir outro programa | vira confirmação pelo fluxo que já existia (`pedir_confirmacao` ganhou `pergunta=` própria); nome exato continua abrindo direto |
| C5 | Clique assistido não considerava a escala de vídeo | com o Windows em 125%/150% (padrão em notebook), o OCR devolve pixel físico e o `pyautogui.click` usa coordenada lógica → **clica em outro lugar** | `configurar_escalas_de_video()` (DPI aware: per-monitor v2 → por monitor → sistema), `escala_de_video()` e `para_coordenadas_de_clique()`; conversão registrada em log |
| C6 | Os dois processos dividiam **um** `RotatingFileHandler` | no Windows a rotação renomeia o arquivo que o outro processo tem aberto: erro na rotação e perda de log justo na hora de investigar | `jarvis_comum.arquivo_de_log()`: um arquivo por processo (`jarvis.log`, `jarvis.tela.log`); `configurar_log` passou a ser idempotente **por nome**; `iniciar_jarvis.ultimas_linhas_do_log()` lê os dois e mistura por horário |

Também saíram dois arquivos que a fila nova (`fila_comandos/`, `fila_eventos/`)
tornou mortos: `comandos_interface.json` e `eventos_interface.json` (nenhuma
linha de código os lia).

Testes novos: `testes/teste_intencoes.py::TesteExtracaoDoArgumento` (10) e, em
`testes/teste_comandos.py`, `TesteNomeDaMusica`, `TesteGerarImagem`,
`TesteEscalaDeVideo`, `TesteCacheDeAplicativos` e `TesteLogPorProcesso` (23).

### Limitação conhecida (documentada, não corrigida)

Um título que **é** um verbo conjugado, dito sem o verbo do comando ("toque de
midas" digitado no painel), perde a primeira palavra na busca — vira "midas". O
caso é ambíguo de verdade (é verbo ou é título?) e a regra ficou conservadora:
só corta quando o resto não diz nada por si. Pela voz o problema não aparece,
porque a frase vem com o verbo ("toca toque de midas").

## Rodada de 16/10/2026 — o cérebro virou agente

O projeto tinha **casca** de Jarvis e **cérebro** de menu de opções: a lista de
frases decidia tudo, e o modelo de linguagem só entrava quando nada era
reconhecido — proibido de agir, ele às vezes *afirmava* ter feito algo que não
fez. Agora o modelo recebe as ferramentas e decide (`jarvis_agente.py`), com o
reconhecedor antigo como caminho rápido.

| # | Antes | Depois |
|---|---|---|
| A1 | Pedido novo não existe na lista e vira conversa ("abre o chrome e pesquisa o preço do ssd" → **abria o chrome com o resto dentro do argumento**) | O laço recebe 16 ferramentas em `tools`, executa **todas** as chamadas do turno, devolve o resultado ao modelo e responde |
| A2 | O modelo afirmava ter executado ação (defesa era uma frase no prompt) | Ele só afirma depois de ver o resultado da ferramenta; a ação aconteceu de verdade porque foi ele que pediu |
| A3 | Ferramenta que estoura derrubava o turno | Toda falha vira texto de resultado (`executar_ferramenta`); teto de 4 rodadas e 6 ferramentas por turno |
| A4 | Ação perigosa por conversa não tinha freio | `corrigir_texto`, `esconder_painel` e `sair_da_jarvis` passam pela confirmação de sim/não que já existia |
| A5 | Fala dupla seria o risco óbvio (função antiga fala + modelo resume) | `fala_sozinha` na ferramenta: quando a função antiga já fala, o texto do modelo não é falado |
| A6 | Modelo/Ollama sem suporte a `tools` quebraria tudo | Recuo automático para um protocolo de texto (JSON no prompt) e, se nem isso, para a conversa antiga |

Arquivos: `jarvis_ferramentas.py` (registro + schema), `jarvis_agente.py` (laço,
auditoria e recuos), `main.py` (as 16 ferramentas e o roteamento híbrido),
`testes/teste_agente.py` (52 testes, com modelo de mentira — sem rede).

**Não entrou nesta rodada:** máquina de estados visual no painel, wake word
local, memória semântica e proatividade — estão nas fases B–E do `VISAO.md`.

## O que continua pendente

* **B15 completo** — thread separada para a câmera (hoje só a mitigação de cadência). É o maior custo que resta na interface: rosto + expressões + mãos rodam no timer da GUI a cada 220 ms.
* **Caminho do "gerar imagem"** — hoje abre uma busca de imagens e avisa que não gera; um gerador local (ComfyUI/A1111) ou API continua por fazer.
* **Memória** — `buscar_memorias_relevantes` é interseção de palavras sobre as últimas 2.000 linhas; não há SQLite/embedding, então o Jarvis não guarda fato nenhum sobre a pessoa.
* **LLM que afirma ter executado ação** — resolvido na rodada de 16/10 para o caminho do agente (ele só fala depois de ver o resultado da ferramenta). Continua valendo para a conversa solta, que não tem ferramenta nenhuma para chamar.
* **CI** — não há GitHub Actions: os 106 testes só rodam quando alguém lembra. `pyproject.toml`/pin de versões também não existem.
* **`import main` tem efeito colateral** — migra a memória legada, configura log e cria o mutex no import; os testes só não sujam o repositório porque o `.gitignore` cobre os arquivos.
* **Reconhecimento offline mais preciso** — instalar `faster-whisper` (`pip install faster-whisper`) ou o modelo pt-BR do Vosk; o app já usa automaticamente se existir.
* **Teste em Windows** — pelo checklist abaixo; os modelos `.task` precisam ser baixados uma vez (`python preparar_modelos.py`).
