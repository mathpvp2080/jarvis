# jarvis

Assistente de voz em português, para Windows: fala, escuta, olha pela câmera
(gestos, humor, holograma) e tem uma interface própria em PySide6 com navegador
embutido, painel do sistema e histórico da conversa.

## Como rodar

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

Se algo não funcionar, **`jarvis.log`** (na pasta do projeto) tem o motivo —
todos os erros são registrados lá, além de aparecerem no painel.

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

Frases parecidas com um comando mas não idênticas fazem o Jarvis **perguntar**
("Você quis dizer ...?") em vez de adivinhar. O resto da conversa vai para o
modelo local (Ollama, `qwen2.5:3b` em `127.0.0.1:11434`).

## Arquivos

| Arquivo | Papel |
|---|---|
| `main.py` | processo de voz: rotas de comando, tela/OCR, música, Ollama, memória |
| `jarvis_tela.py` | interface: câmera, gestos, holograma, navegador, chat, bandeja |
| `iniciar_jarvis.py` | abre os dois processos e cuida deles |
| `jarvis_comum.py` | log, JSON atômico, filas entre os processos (sem corrida) |
| `jarvis_audio.py` | microfone + detecção de voz adaptativa (thread própria) |
| `jarvis_voz.py` | reconhecimento com plano B: Google → Whisper → Vosk → Windows |
| `jarvis_intencoes.py` | entende a fala: sinônimos, parecença e confirmação |
| `jarvis_tts.py` | fala em processo persistente (com interrupção) |
| `preparar_modelos.py` | baixa/copia os modelos da câmera |
| `DIAGNOSTICO.md` | diagnóstico técnico completo + status do que foi corrigido |
| `config_jarvis.json` | preferências (câmera, gestos, holograma, microfone, voz) |
| `memoria_jarvis.jsonl` | histórico da conversa (uma linha por mensagem) |

## Testes

Rodam em qualquer sistema (não precisam de Windows, microfone nem câmera):

```powershell
python -m unittest discover -s testes -t .
```

73 testes: detecção de voz, cadeia de reconhecimento, camada de intenções,
rotas de comando do `main.py` (com 23 comandos reais do histórico), protocolo
da fala e uma fumaça da interface. `testes/comparar_antigo_novo.py` mostra o
antes/depois do detector de voz lado a lado.

## Configurações do painel

Engrenagem / menu → **Configurações**: câmera, gestos, holograma, emoções,
bloqueio por ausência (avisa 10 s antes), **microfone** e **motor de voz**.
Arquivos `*.task` da câmera: rode `python preparar_modelos.py` se o painel
avisar que faltam.
