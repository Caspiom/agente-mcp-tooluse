# PROGRESSO — agente-mcp-tooluse

Último ponto onde parei (atualize ao fim de cada sessão de 1h):

- **Data:** 2026-11-03 (bloco de TERÇA, feito pelo Hermes a pedido do Lucas, ~20h)
- **Onde parei:** teto de custo agora é DURO. Antes de cada chamada à política o agente estima o pior caso do passo (entrada + `max_output_tokens`) e para sem chamar o modelo se `gasto + pior caso > teto` (span `budget_block`). `g07`: custo final US$ 0,000666 -> 0,000237 (teto 0,0004). Novo `g09` (teto menor que um passo: custo 0, modelo nunca chamado) e invariante `custo final <= teto` conferida em TODOS os casos. Eval 22/22, 39 testes (3 rodadas com `-W error`/PYTHONHASHSEED). 5 mutantes novos da pré-checagem: todos pegos pelos testes, 3 pelo eval (histórico e margem só pelos testes).
- **Achados honestos no README:** garantia vale só se a política respeitar `max_output_tokens` (teste `limite_conhecido`) e é relativa ao estimador chars/4 (`safety_margin` p/ tokenizer real); o pior caso é conservador, então teto e `max_output_tokens` têm de ser dimensionados juntos.
- **Próximo passo:** (1) política com LLM real (provedor gratuito/fixture primeiro; estimar custo e pedir ok antes de gastar), comparada com a de regras no mesmo eval, medindo planejamento, não só proteções; (2) caso de eval a poucos tokens do teto para matar os mutantes de histórico/margem; (3) tracing em OpenTelemetry/LangSmith; (4) transporte HTTP do MCP e mais ferramentas.
