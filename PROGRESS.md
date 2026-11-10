# PROGRESSO — agente-mcp-tooluse

Último ponto onde parei (atualize ao fim de cada sessão de 1h):

- **Data:** 2026-11-10 (bloco de TERÇA 10/11, feito pelo Hermes a pedido do Lucas, à noite)
- **Onde parei:** (1) 4 casos de eval a 1 a 1,5 µUSD da borda do teto (g10, g11 barram; c14, c15 passam raspando, sem e com `safety_margin`), borda achada por bisseção. Os 5 mutantes de custo agora reprovam o eval (antes histórico e margem só caíam nos testes). (2) `LlmPolicy`: prompt com catálogo de ferramentas, parser defensivo (JSON puro, cercado ou com texto em volta) e `policy_error` para saída ruim, sem chamar ferramenta. O agente agora cobra o prompt inteiro e o texto bruto do modelo (`build_prompt`/`raw`). (3) `scripts/mutacao.py`: 9 mutantes reproduzíveis, 0 sobreviventes. 59 testes, 26/26 no eval, 3 rodadas estáveis com `-W error`.
- **Limites honestos:** nenhum LLM real foi chamado (só modelo falso), então não há medida de qualidade de planejamento; os 4 mutantes da LlmPolicy só os testes pegam (o eval usa a política de regras); estimador continua chars/4.
- **Próximo passo:** (1) chamar um LLM de verdade com teto duro (provedor grátis ou fixture primeiro; estimar custo e pedir ok antes de gastar) e rodar o MESMO eval com `LlmPolicy`, medindo planejamento; (2) tracing em OpenTelemetry/LangSmith; (3) transporte HTTP do MCP e mais ferramentas.
