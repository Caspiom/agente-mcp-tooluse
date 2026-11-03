# agente-mcp-tooluse

> **Pilar do portfólio — Terça** · parte de uma série de 7 projetos de Engenharia de Software de IA (1h/dia).

## O problema
Um agente que usa ferramentas expostas por um **servidor MCP**, e que **não quebra** quando a ferramenta falha, demora, ou quando o gasto sai do controle: retry com backoff só onde faz sentido, teto de custo, tracing e um eval que prova que as proteções funcionam.

## O que tem (e o que NÃO tem ainda)
- **Tem:**
  - **Servidor MCP próprio** (JSON-RPC 2.0 por stdio: `initialize`, `tools/list`, `tools/call`, `ping`) com 5 ferramentas, incluindo um backend instável de propósito.
  - **Cliente MCP** com timeout real.
  - **Agente ReAct** (Thought → Action → Observation) com retry/backoff, teto de custo, limite de passos, detecção de loop e tracing em JSONL.
  - **Eval** com gabarito escrito à mão.
  - Tudo **offline, grátis e determinístico**.
- **Provado contra o SDK oficial:** `scripts/interop_sdk.py` conecta o cliente do SDK `mcp` (v2.3.0) ao servidor, lista as 5 ferramentas e chama duas. Ou seja, o protocolo está certo, não só "funciona com o meu cliente".
- **Não tem (honestidade):**
  - **A política é regra, não LLM.** `ScriptedPolicy` lê a pergunta com regex e monta o plano. O contrato (`decide(pergunta, scratchpad) -> Action | Final`) é o de um LLM, então trocar é substituir uma classe. **Por isso o eval mede as proteções do agente (retry, custo, erros), não a inteligência do planejamento.**
  - **O custo é estimado** (tokens ≈ caracteres/4, preço ilustrativo US$3/M entrada e US$15/M saída), não faturado.
  - Sem LangGraph, LangSmith nem MCP SDK no caminho principal (o SDK só entra na prova de interop). O servidor MCP é o mínimo do protocolo: sem resources, prompts nem transporte HTTP.

## Resultados medidos (21 casos, gabarito manual)
| Categoria | Aprovados |
|---|---|
| cálculo / conversão / câmbio / estoque / KB | 10/10 |
| encadeado (usa o resultado da ferramenta anterior) | 2/2 |
| retry (backend falha 2× e depois responde) | 1/1 |
| guardrails (injeção, ÷0, unidade inválida, item inexistente, backend fora, sem ferramenta, teto de custo) | 8/8 |
| **Total** | **21/21** |

- **Testes:** 34 passando, estáveis em 3 execuções seguidas com `-W error`.
- **Retries:** 4 no total, com backoff exponencial medido `[0.2 s, 0.4 s]`.
- **Latência por chamada MCP** (processo local): p50 ≈ 0,3 ms, p95 ≈ 0,5 ms.
- **Custo estimado médio:** ≈ US$ 0,00044 por pergunta.
- **Trace de exemplo** (retry em ação): `docs/exemplo-trace-retry.jsonl`.

### Como as proteções se comportam
- **Retry só no que é transitório.** `TRANSIENT` (e timeout do MCP) é repetido até 3 tentativas. `INVALID` e `NOT_FOUND` **não** são repetidos (repetir só gasta). Backend sempre fora → `tool_failed` após 3 tentativas, sem loop infinito.
- **Teto de custo bloqueia a ação, não o gasto já feito.** O custo é conferido *antes* de executar a ferramenta. Mas a chamada de política que estourou o teto já foi paga: no caso `g07` o teto era US$ 0,0004 e o custo final foi US$ 0,000666. Um teto "duro" de verdade precisaria estimar o custo *antes* de chamar o modelo.
- **A calculadora nunca usa `eval`.** Ela caminha pela árvore sintática e só aceita números, `+ - * /` e parênteses (sem `**`, nomes, chamadas ou atributos). `__import__('os').getpid()` volta como `INVALID`.

## Validação por mutação
Quebrei o código de propósito, 9 mutantes. **Os 34 testes pegam os 9.** O **eval** pega 8 deles; o que escapa só é pego pelos testes unitários:

| Mutante | Testes que falham | Casos do eval que falham |
|---|---|---|
| calculadora usa `eval()` | 5 | g08 |
| sem retry | 4 | c09, g05 |
| teto de custo desligado | 1 | g07 |
| repete erro permanente | 1 | g01, g02, g03, g04, g08 |
| sem backoff | 3 | c09, g05 |
| detecção de loop desligada | 1 | **nenhum** (o `ScriptedPolicy` nunca entra em loop; só o teste com política maliciosa pega) |
| encadeamento ignora a observação | 1 | c12, c13 |
| `TRANSIENT` tratado como permanente | 3 | c09, g05 |
| `NOT_FOUND` vira transitório | 1 | g04 |

(Os testes que falham em cada mutante ficam entre 1 e 5. Com `eval()`, o `9**9**9` do teste chegou a travar ~12 s numa rodada, que é o DoS real que a calculadora segura evita.)

## Furos que a validação por mutação revelou nos meus próprios testes
1. **Injeção mascarada.** Meu primeiro caso de injeção (`getcwd()`) devolvia uma *string*, e o formatador numérico quebrava nela por acaso, então o bug do `eval()` passava despercebido. Troquei por `getpid()`, que devolve número e expõe o bug (caso `g08`).
2. **Backoff não conferido.** O eval só contava retries e nunca os tempos de espera. Agora `c09` e `g05` exigem `[0.2, 0.4]`.
3. **O mesmo mascaramento nos testes unitários.** Com `eval()`, só `9**9**9` e `x+1` derrubavam os testes; nenhuma injeção `__import__` o fazia. Adicionei `getpid()`, `abs(-1)` e `len('abc')` (resultado numérico), e agora 5 testes pegam esse mutante.

E um bug de recurso: o cliente deixava o `stdout` do subprocesso aberto. `pytest -W error` mostrou, e o `close()` agora fecha os pipes e junta a thread leitora.

## Como rodar (< 5 min)
```bash
make setup    # cria .venv e instala pytest
make all      # 34 testes + eval de 21 casos
python -m evals.run_eval --json                # métricas em JSON
python -m evals.run_eval --trace-dir /tmp/tr   # um trace .jsonl por caso

# prova de interop com o SDK oficial (opcional, instala o SDK fora do repo)
python3 -m venv /tmp/mcp-interop && /tmp/mcp-interop/bin/pip install mcp
/tmp/mcp-interop/bin/python scripts/interop_sdk.py
```

## Estrutura
- `src/mcp_server.py` — servidor MCP (JSON-RPC/stdio) + 5 ferramentas
- `src/mcp_client.py` — cliente com timeout
- `src/agent.py` — loop ReAct, retry, teto de custo, tracing
- `src/policy.py` — política (regra; ponto de troca por LLM)
- `evals/casos.jsonl` — gabarito manual · `evals/run_eval.py` — métricas
- `tests/test_agente.py` — protocolo, ferramentas, agente, tracing
- `scripts/interop_sdk.py` — prova de interop com o SDK oficial

## Status
- [x] Esqueleto / setup roda em < 15 min
- [x] Núcleo funcional (MCP + agente + proteções)
- [x] Avaliação / métricas medidas (21 casos, mutação)
- [x] README com números e limitações
- [ ] Política com LLM real (com teto de custo) e comparação contra a de regras no mesmo eval
- [ ] LangGraph / tracing em LangSmith ou OpenTelemetry
- [ ] Transporte HTTP do MCP, mais ferramentas; demo reproduzível

---
Autor: Gappy · Série montada com apoio do Hermes
