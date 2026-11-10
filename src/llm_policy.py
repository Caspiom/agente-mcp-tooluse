"""Politica guiada por modelo de linguagem. `complete(prompt) -> str` e quem fala com o modelo (HTTP, SDK,
ou um falso nos testes); esta classe monta o prompt, interpreta a resposta e NUNCA confia nela:
JSON quebrado, ferramenta fora da lista ou argumentos que nao sao objeto viram `policy_error`,
sem chamar ferramenta nenhuma."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

from .policy import Action, Final, Step

FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(raw: str) -> dict:
    fenced = FENCE.search(raw)
    candidate = fenced.group(1) if fenced else raw
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("sem objeto JSON na resposta")
    value = json.loads(candidate[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("JSON nao e um objeto")
    return value


@dataclass
class LlmPolicy:
    complete: Callable[[str], str]
    tools: list[dict]
    name: str = "llm"

    def build_prompt(self, question: str, scratch: list[Step]) -> str:
        catalog = "\n".join(f"- {t['name']}: {t.get('description', '')} schema={json.dumps(t['inputSchema'])}"
                            for t in self.tools)
        history = "".join(f"\nAction: {s.action.tool} {json.dumps(s.action.args)}\nObservation: {s.observation}"
                          for s in scratch)
        return (f"Ferramentas disponiveis:\n{catalog}\n\n"
                "Responda SOMENTE com JSON: {\"tool\": nome, \"args\": {...}} para usar uma ferramenta, "
                "ou {\"final\": texto} para responder.\n\n"
                f"Pergunta: {question}{history}")

    def decide(self, question: str, scratch: list[Step]) -> Action | Final:
        raw = self.complete(self.build_prompt(question, scratch))
        try:
            data = extract_json(raw)
        except ValueError as e:
            return Final(f"Resposta invalida do modelo: {e}", status="policy_error", raw=raw)
        if isinstance(data.get("final"), str):
            return Final(data["final"], raw=raw)
        tool, args = data.get("tool"), data.get("args", {})
        if tool not in {t["name"] for t in self.tools}:
            return Final(f"O modelo pediu uma ferramenta que nao existe: {tool!r}", status="policy_error", raw=raw)
        if not isinstance(args, dict):
            return Final("Os argumentos da ferramenta nao sao um objeto JSON.", status="policy_error", raw=raw)
        return Action(tool, args, str(data.get("thought", "")), raw=raw)
