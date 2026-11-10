"""Mutacao do teto de custo e da politica de LLM: aplica cada mutante numa COPIA do repo, roda os testes e
o eval e mostra quem pegou. Uso: .venv/bin/python scripts/mutacao.py"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

ORIGINAL = "return (tokens(input_text(question, scratch, self.policy)) * PRICE_IN + self.max_out * PRICE_OUT) * self.margin"
AGENT, LLM = "src/agent.py", "src/llm_policy.py"
MUTANTES = {
    "sem pre-checagem": (AGENT, "if cost + worst > self.max_cost:", "if False:"),
    "pior caso ignora a saida": (AGENT, ORIGINAL, ORIGINAL.replace("+ self.max_out * PRICE_OUT", "")),
    "pior caso ignora o historico": (AGENT, ORIGINAL, ORIGINAL.replace("input_text(question, scratch, self.policy)", "question")),
    "ignora o gasto acumulado": (AGENT, "if cost + worst > self.max_cost:", "if worst > self.max_cost:"),
    "ignora a margem": (AGENT, ORIGINAL, ORIGINAL.replace(" * self.margin", "")),
    "custo ignora o prompt do LLM": (AGENT, "    if policy is not None and hasattr(policy, \"build_prompt\"):", "    if False:"),
    "custo ignora o texto bruto": (AGENT, "text_out = decision.raw or (", "text_out = (\"\") or ("),
    "aceita ferramenta fora da lista": (LLM, "if tool not in {t[\"name\"] for t in self.tools}:", "if False:"),
    "aceita args que nao e objeto": (LLM, "if not isinstance(args, dict):", "if False:"),
}


def roda(pasta: Path) -> tuple[int, list[str]]:
    t = subprocess.run([PY, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"],
                       cwd=pasta, capture_output=True, text=True)
    e = subprocess.run([PY, "-m", "evals.run_eval"], cwd=pasta, capture_output=True, text=True)
    return t.returncode, re.findall(r"FALHA (\w+)", e.stdout)


def main() -> int:
    sobreviveu = 0
    for nome, (arquivo, antes, depois) in MUTANTES.items():
        with tempfile.TemporaryDirectory() as d:
            copia = Path(d) / "repo"
            shutil.copytree(ROOT, copia, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache"))
            alvo = copia / arquivo
            fonte = alvo.read_text()
            assert fonte.count(antes) == 1, nome
            alvo.write_text(fonte.replace(antes, depois))
            codigo_testes, casos = roda(copia)
        pego_testes, pego_eval = codigo_testes != 0, bool(casos)
        sobreviveu += not (pego_testes or pego_eval)
        print(f"{nome:<32} testes: {'PEGOU' if pego_testes else 'passou'}   eval: {', '.join(casos) or 'passou'}")
    print("sobreviveram:", sobreviveu)
    return 1 if sobreviveu else 0


if __name__ == "__main__":
    sys.exit(main())
