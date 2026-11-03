# PROGRESSO — agente-mcp-tooluse

Último ponto onde parei (atualize ao fim de cada sessão de 1h):

- **Data:** 2026-11-03 (bloco de TERÇA, feito pelo Hermes a pedido do Lucas, ~00h30)
- **Onde parei:** servidor MCP próprio (5 ferramentas) + cliente com timeout + agente ReAct com retry/backoff só para erro transitório, teto de custo, limite de passos, detecção de loop e tracing JSONL. Eval com 21 casos de gabarito manual: 21/21. 34 testes (estáveis com `-W error`). 9 mutantes: todos pegos pelos testes, 8 pelo eval (o de loop só pelos testes). Interop provado com o cliente oficial do SDK `mcp` 2.3.0. Política é REGRA (regex), não LLM.
- **Achados honestos no README:** o teto de custo bloqueia a ação mas a chamada que estourou já foi paga (US$ 0,000666 vs teto 0,0004); o primeiro caso de injeção estava mascarado (devolvia string) e o eval não conferia backoff, ambos corrigidos.
- **Próximo passo:** (1) política com LLM real, com teto de custo e estimativa antes de gastar, comparada com a de regras no mesmo eval (aí o eval passa a medir planejamento, não só proteções); (2) estimar o custo ANTES da chamada para o teto ser duro; (3) tracing em OpenTelemetry/LangSmith; (4) transporte HTTP do MCP e mais ferramentas.
