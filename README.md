# jarvis

Assistente de voz em português, para Windows: fala, escuta, olha pela câmera
(gestos, humor, holograma) e tem uma interface própria em PySide6 com navegador
embutido, painel do sistema e histórico da conversa.

## Como instalar (dois cliques)

1. Instale o **[Python 3.12](https://www.python.org/downloads/)** — marque
   **"Add Python to PATH"** na primeira tela. (Se pular isso, o `instalar.bat`
   avisa e abre a página certa.)
2. Baixe o projeto e descompacte.
3. Dois cliques em **`instalar.bat`** — ele cria o ambiente, instala as
   dependências, baixa os modelos da câmera e confere o Ollama.
4. Depois, dois cliques em **`iniciar.bat`** para abrir.

O [Ollama](https://ollama.com/download) é o único item que não dá para instalar
por aqui (é um instalador próprio); o `instalar.bat` avisa se ele faltar e abre
a página de download. O modelo de conversa (`qwen2.5:3b`) ele baixa sozinho.

## Como rodar (na mão)

```powershell
cd <pasta do jarvis>

# 1) Dependências (Python 3.11 ou 3.12)
pip install -r requirements.txt

# 2) Modelos da câmera (uma vez; sem internet para o modelo de rosto)
python preparar_modelos.py

# 3) Abrir
python iniciar_jarvis.py
```

O `iniciar_jarvis.py` confere o ambiente, sobe o processo de voz (`main.py`) e
a interface (`jarvis_tela.py`) e encerra os dois se um deles cair.

Se algo não funcionar, os logs (na pasta do projeto) têm o motivo — todos os
erros são registrados lá, além de aparecerem no painel:

* **`jarvis.log`** — processo de voz (`main.py`)
* **`jarvis.tela.log`** — interface (`jarvis_tela.py`)

## O que ele entende

Fale *"Jarvis"* e depois o pedido. Exemplos que funcionam de verdade:

| Você fala | O que acontece |
|---|---|
| "que horas são?" | hora, sem consultar a IA |
| "abre o chrome" / "roda a steam" | abre o programa (variações do verbo funcionam) |
| "ta vendo minha tela?" | descreve a tela com a IA local |
| "aumenta o volume", "próxima música", "silenciar" | teclas de mídia do Windows |
| "toca back in black no youtube music" | toca no navegador do painel |
| "pesquisar buracos negros" | pesquisa no navegador do painel |
| "liga o holograma" / "tira holograma para mim" | cubo 3D controlado por gestos |
| "esconder painel" | esconde a janela na bandeja |
| "corrigir e colar" | corrige o texto copiado com a IA |
| "clicar em confirmar" | clique assistido (pede confirmação antes) |
| "quando eu disser X faça Y" | ensina um comando novo |
| "abre o chrome **e** pesquisa o preço do ssd" | duas ações na mesma frase |
| "o que você sabe fazer?" | lista as ferramentas disponíveis |

Frases parecidas com um comando mas não idênticas fazem o Jarvis **perguntar**
("Você quis dizer ...?") em vez de adivinhar.

### Cérebro com ferramentas (o agente)

O que **não** é um comando conhecido vai para o modelo local
(Ollama, `qwen2.5:3b` em `127.0.0.1:11434`) — e agora o modelo não só conversa:
ele recebe a lista de ferramentas e **decide** o que fazer, encadeando quantas
precisar. É isso que faz "abre o chrome e pesquisa o preço do ssd" funcionar sem
ter uma frase cadastrada para isso.

* **Caminho rápido primeiro.** Pedido que o reconhecedor entende com confiança
  alta (e que não seja encadeado) executa direto, sem esperar o modelo:
  "aumenta o volume" continua instantâneo.
* **O modelo não mente mais.** Ele só afirma ter feito algo depois de ver o
  resultado da ferramenta — e a fala das ações continua sendo a das funções
  antigas, sem fala dupla.
* **Ação que mexe no seu computador pede confirmação** (`corrigir_texto`,
  `esconder_painel`, `sair_da_jarvis`), pelo mesmo fluxo de sim/não de antes.
* **Teto de segurança:** no máximo 4 rodadas e 6 ferramentas por turno; cada
  ferramenta que estoura vira mensagem de erro em vez de derrubar o laço.
* **Se o Ollama estiver fora, ou o modelo não souber chamar ferramenta**, tudo
  volta para o comportamento antigo (conversa simples) — inclusive com um
  protocolo de texto para modelos sem suporte a `tools`.

Para desligar e voltar ao comportamento de comandos, ponha
`"agente_ativado": false` no `config_jarvis.json`. Com 8 GB de RAM, vale trocar
`"modelo_local"` por um modelo melhor em ferramentas (`ollama pull qwen2.5:7b`).

As ferramentas estão em `jarvis_ferramentas.py` (o que existe) e o laço de
decisão em `jarvis_agente.py` (como decide). O registro sai em JSON
(`registro.como_json()`), que é o que um cliente no celular consumiria depois.

## Arquivos

| Arquivo | Papel |
|---|---|
| `main.py` | processo de voz: rotas de comando, tela/OCR, música, Ollama, memória |
| `jarvis_tela.py` | interface: câmera, gestos, holograma, navegador, chat, bandeja |
| `instalar.bat` / `iniciar.bat` | instalação e abertura com dois cliques no Windows |
| `instalar_jarvis.py` | os passos do instalador (modelos da câmera, Ollama, Tesseract) |
| `iniciar_jarvis.py` | abre os dois processos e cuida deles |
| `jarvis_comum.py` | log, JSON atômico, filas entre os processos (sem corrida) |
| `jarvis_audio.py` | microfone + detecção de voz adaptativa (thread própria) |
| `jarvis_voz.py` | reconhecimento com plano B: Google → Whisper → Vosk → Windows |
| `jarvis_intencoes.py` | entende a fala: sinônimos, parecença e confirmação |
| `jarvis_ferramentas.py` | as ferramentas da Jarvis, no formato que o modelo entende |
| `jarvis_agente.py` | o laço que decide: chama ferramenta, lê o resultado, responde |
| `jarvis_tts.py` | fala em processo persistente (com interrupção) |
| `preparar_modelos.py` | baixa/copia os modelos da câmera |
| `DIAGNOSTICO.md` | diagnóstico técnico completo + status do que foi corrigido |
| `jarvis.log` / `jarvis.tela.log` | log de cada processo (voz / interface), rotativos |
| `config_jarvis.json` | preferências (câmera, gestos, holograma, microfone, voz, agente) |
| `VISAO.md` | para onde o projeto vai: comparação com os Jarvis da internet |
| `memoria_jarvis.jsonl` | histórico da conversa (uma linha por mensagem) |

## Testes

Rodam em qualquer sistema (não precisam de Windows, microfone nem câmera):

```powershell
python -m unittest discover -s testes -t .
```

188 testes: detecção de voz, cadeia de reconhecimento, camada de intenções
(inclusive a extração do argumento — o nome da música, o termo da busca, a
descrição da imagem), rotas de comando do `main.py` (com 23 comandos reais do
histórico, cache de aplicativos, confirmação de nome parecido e escala de vídeo),
protocolo da fala, log por processo, o registro de ferramentas, o laço do agente
(com um modelo de mentira, sem rede), o orçamento de tempo do turno, o
instalador e uma fumaça da interface.
`testes/comparar_antigo_novo.py` mostra o antes/depois do detector de voz lado a
lado.

## Configurações do painel

Engrenagem / menu → **Configurações**: câmera, gestos, holograma, emoções,
bloqueio por ausência (avisa 10 s antes), **microfone** e **motor de voz**.
Arquivos `*.task` da câmera: rode `python preparar_modelos.py` se o painel
avisar que faltam.
