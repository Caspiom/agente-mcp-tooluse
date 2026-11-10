"""Agente ReAct (Thought -> Action -> Observation) com tres protecoes de producao:

  1. RETRY com backoff exponencial, SO para erros transitorios (TRANSIENT/timeout).
     Erro permanente (INVALID/NOT_FOUND) NAO e repetido: repetir so gasta custo.
  2. TETO DE CUSTO DURO: ANTES de cada chamada ao modelo (politica) o agente estima o PIOR caso
     do passo (entrada conhecida + max_output_tokens, o max_tokens que um provedor imporia).
     Se gasto + pior caso > teto, para SEM chamar o modelo. Assim o custo final <= teto, desde
     que a politica respeite max_output_tokens. Ha ainda uma checagem DEPOIS da chamada (defesa em
     profundidade): so dispara se a politica estourar o proprio max_output_tokens.
  3. TRACING: cada decisao da politica e cada tentativa de ferramenta viram um span
     (JSONL) com latencia, custo, tentativa e status.

Mais: limite de passos e deteccao de loop (mesma ferramenta+argumentos repetida).

Custo estimado: tokens ~ len(texto)/4; preco ilustrativo (US$3/M entrada, US$15/M saida).
O agente nao chama LLM nenhum aqui, mas contabiliza como se chamasse, para o teto ser testavel.
Limite do estimador: chars/4 e o mesmo contador do livro-caixa, entao "duro" vale em relacao a ele;
com tokenizer real, use safety_margin > 1."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .mcp_client import McpClosed, McpTimeout
from .policy import Action, Final, Step

PRICE_IN, PRICE_OUT = 3.0 / 1e6, 15.0 / 1e6


def tokens(s: str) -> int:
    return max(1, len(s) // 4)


def input_text(question: str, scratch: list, policy=None) -> str:
    """Texto que o modelo recebe neste passo. Politica com `build_prompt` (LLM) paga o prompt inteiro,
    ferramentas incluidas; a de regras so a pergunta + historico acao/observacao."""
    if policy is not None and hasattr(policy, "build_prompt"):
        return policy.build_prompt(question, scratch)
    return question + "".join(f" {s.action.tool} {s.observation}" for s in scratch)


@dataclass
class Result:
    status: str          # ok | no_tool | policy_error | tool_error | tool_failed | budget_exceeded | max_steps | loop_detected
    answer: str
    steps: int
    retries: int
    cost_usd: float
    tools_called: list[str]
    trace: list[dict] = field(default_factory=list)


class Tracer:
    def __init__(self):
        self.trace_id = uuid.uuid4().hex[:8]
        self.spans: list[dict] = []

    def add(self, **kw) -> None:
        self.spans.append({"trace_id": self.trace_id, "span_id": len(self.spans) + 1, **kw})

    def dump_jsonl(self, path: str | Path) -> None:
        Path(path).write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in self.spans))


class Agent:
    def __init__(self, client, policy, *, max_steps: int = 6, max_cost_usd: float = 0.01,
                 max_attempts: int = 3, backoff_s: float = 0.2, sleep=time.sleep,
                 max_output_tokens: int = 256, safety_margin: float = 1.0):
        self.client, self.policy = client, policy
        self.max_steps, self.max_cost = max_steps, max_cost_usd
        self.max_out, self.margin = max_output_tokens, safety_margin
        self.max_attempts, self.backoff = max_attempts, backoff_s
        self.sleep = sleep

    # ------------------------------------------------------------ ferramenta
    def _call_with_retry(self, act: Action, tr: Tracer, step: int):
        """Devolve (texto, kind, retries) com kind em ok | permanent | transient_exhausted | closed."""
        retries, text = 0, ""
        for attempt in range(1, self.max_attempts + 1):
            t0 = time.perf_counter()
            try:
                text, is_err = self.client.call_tool(act.tool, act.args)
                kind = "ok" if not is_err else ("transient" if text.startswith("TRANSIENT") else "permanent")
            except McpTimeout:
                text, kind = "TRANSIENT: timeout do servidor MCP", "transient"
            except McpClosed:
                tr.add(type="tool", step=step, tool=act.tool, attempt=attempt, status="closed", latency_ms=0.0)
                return "servidor MCP encerrou", "closed", retries
            ms = round((time.perf_counter() - t0) * 1000, 2)
            tr.add(type="tool", step=step, tool=act.tool, args=act.args, attempt=attempt, latency_ms=ms,
                   status=kind, output=text[:120])
            if kind in ("ok", "permanent"):
                return text, kind, retries
            if attempt < self.max_attempts:
                delay = self.backoff * 2 ** (attempt - 1)
                tr.add(type="backoff", step=step, tool=act.tool, delay_s=delay)
                self.sleep(delay)
                retries += 1
        return text, "transient_exhausted", retries

    # ------------------------------------------------------------------ custo
    def worst_case_usd(self, question: str, scratch: list) -> float:
        """Pior caso do proximo passo: entrada ja conhecida + saida no limite de max_output_tokens.
        `safety_margin` > 1 cobre a diferenca entre o estimador (chars/4) e o tokenizer real."""
        return (tokens(input_text(question, scratch, self.policy)) * PRICE_IN + self.max_out * PRICE_OUT) * self.margin

    # ------------------------------------------------------------------ loop
    def run(self, question: str) -> Result:
        tr, scratch, cost, retries, seen = Tracer(), [], 0.0, 0, set()
        called: list[str] = []

        def done(status, answer):
            return Result(status, answer, len(scratch), retries, round(cost, 6), called, tr.spans)

        for i in range(1, self.max_steps + 1):
            worst = self.worst_case_usd(question, scratch)
            if cost + worst > self.max_cost:          # estima ANTES de pagar: nao chama o modelo
                tr.add(type="budget_block", step=i, cost_acumulado=round(cost, 6),
                       est_pior_caso_usd=round(worst, 6), teto_usd=self.max_cost)
                return done("budget_exceeded", f"Interrompido antes de chamar o modelo: o proximo passo poderia "
                                               f"custar ate US$ {worst:.6f} e passaria do teto (US$ {self.max_cost}).")
            decision = self.policy.decide(question, scratch)
            text_in = input_text(question, scratch, self.policy)
            text_out = decision.raw or (json.dumps({"tool": decision.tool, "args": decision.args})
                                        if isinstance(decision, Action) else decision.text)
            c = tokens(text_in) * PRICE_IN + tokens(text_out) * PRICE_OUT
            cost += c
            tr.add(type="policy", step=i, decision=type(decision).__name__.lower(),
                   thought=getattr(decision, "thought", ""), est_pior_caso_usd=round(worst, 6),
                   cost_usd=round(c, 6), cost_acumulado=round(cost, 6))
            if cost > self.max_cost:
                return done("budget_exceeded", f"Interrompido: a politica estourou max_output_tokens e o teto "
                                               f"(US$ {self.max_cost}) foi ultrapassado.")
            if isinstance(decision, Final):
                return done(decision.status, decision.text)
            key = (decision.tool, json.dumps(decision.args, sort_keys=True))
            if key in seen:
                return done("loop_detected", "Interrompido: a mesma acao foi repetida.")
            seen.add(key)
            text, kind, r = self._call_with_retry(decision, tr, i)
            retries += r
            called.append(decision.tool)
            if kind == "ok":
                scratch.append(Step(decision, text))
                continue
            if kind == "permanent":
                return done("tool_error", f"A ferramenta {decision.tool} recusou o pedido: {text}")
            return done("tool_failed", f"A ferramenta {decision.tool} falhou apos {self.max_attempts} tentativas.")
        return done("max_steps", "Interrompido: limite de passos atingido.")
