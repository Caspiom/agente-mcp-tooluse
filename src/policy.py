"""Politica de decisao do agente (o 'cerebro' do ReAct).

`ScriptedPolicy` e REGRA, nao LLM: le a pergunta com regex, monta um plano de ferramentas e
encadeia observacoes ($prev). Serve para o agente ser deterministico, offline e testavel de
graca. O contrato (`decide(question, scratch) -> Action | Final`) e o mesmo que um LLM
implementaria; trocar e substituir esta classe, sem mexer em retry/custo/tracing."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Action:
    tool: str
    args: dict
    thought: str = ""
    raw: str = ""


@dataclass
class Final:
    text: str
    status: str = "ok"
    raw: str = ""


@dataclass
class Step:
    """Um par acao/observacao do scratchpad."""
    action: Action
    observation: str


_CUR = r"(USD|EUR|BRL)"
_UNIT = r"(km|mi|kg|lb|c|f)"
_NUM = r"(\d+(?:[.,]\d+)?)"

CHAIN = re.compile(rf"calcule\s+(?P<expr>.+?)\s+e depois converta o resultado de\s+(?P<a>{_CUR})\s+para\s+(?P<b>{_CUR})", re.I)
CUR = re.compile(rf"(?:converta|quanto [eé])\s+(?P<v>{_NUM})\s*(?P<a>{_CUR})\s+(?:em|para)\s+(?P<b>{_CUR})\b", re.I)
UNIT = re.compile(rf"converta\s+(?P<v>{_NUM})\s*(?P<a>{_UNIT})\s+(?:para|em)\s+(?P<b>{_UNIT})\b", re.I)
INV = re.compile(r"estoque d[oe]\s+(?:item|produto)\s+(?P<sku>[A-Za-z0-9-]+)", re.I)
KB = re.compile(r"pol[ií]tica", re.I)
CALC = re.compile(r"^(?:quanto [eé]|calcule)\s+(?P<expr>.+?)\??$", re.I)


@dataclass
class ScriptedPolicy:
    name: str = "scripted"
    _cache: dict = field(default_factory=dict, repr=False)

    def plan(self, q: str) -> list[Action]:
        q = q.strip()
        if m := CHAIN.search(q):
            return [Action("calculator", {"expression": m["expr"]}, "primeiro calculo o valor"),
                    Action("currency_convert", {"amount": "$prev", "from_currency": m["a"].upper(),
                                                "to_currency": m["b"].upper()}, "agora converto o resultado")]
        if m := CUR.search(q):
            return [Action("currency_convert", {"amount": m["v"], "from_currency": m["a"].upper(),
                                                "to_currency": m["b"].upper()}, "pedido de cambio")]
        if m := UNIT.search(q):
            return [Action("unit_convert", {"value": m["v"], "from_unit": m["a"].lower(),
                                            "to_unit": m["b"].lower()}, "pedido de conversao de unidade")]
        if m := INV.search(q):
            return [Action("inventory_check", {"sku": m["sku"].upper()}, "consulta de estoque")]
        if KB.search(q):
            return [Action("kb_search", {"query": q}, "pergunta sobre politica interna")]
        if m := CALC.match(q):
            return [Action("calculator", {"expression": m["expr"]}, "conta aritmetica")]
        return []

    def decide(self, question: str, scratch: list[Step]) -> Action | Final:
        plan = self.plan(question)
        i = len(scratch)
        if not plan:
            return Final("Nao tenho ferramenta para responder isso.", status="no_tool")
        if i < len(plan):
            act = plan[i]
            args = dict(act.args)
            if args.get("amount") == "$prev":
                args["amount"] = scratch[-1].observation.split()[0]
            return Action(act.tool, args, act.thought)
        return Final(self._compose(plan, scratch))

    @staticmethod
    def _compose(plan: list[Action], scratch: list[Step]) -> str:
        last = scratch[-1]
        a = last.action
        obs = last.observation
        if a.tool == "calculator":
            return f"O resultado e {obs}."
        if a.tool == "unit_convert":
            return f"{a.args['value']} {a.args['from_unit']} = {obs}."
        if a.tool == "currency_convert":
            return f"{a.args['amount']} {a.args['from_currency']} = {obs}."
        if a.tool == "inventory_check":
            return f"Estoque de {obs}."
        return obs
