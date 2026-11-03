"""Avaliacao do agente. Gabarito escrito A MAO em evals/casos.jsonl (nao calculado pelo codigo testado).

Por caso: sobe um servidor MCP novo (contadores do 'estoque instavel' zerados), roda o agente
com sleep falso (registra o backoff sem esperar) e compara com o gabarito:
  - status esperado (ok por padrao)        - resposta contem o valor certo
  - sequencia exata de ferramentas         - numero de retries (quando especificado)
  - proibido aparecer na resposta (vazamento)

Uso: python -m evals.run_eval [--json] [--trace-dir DIR]"""
import json
import statistics
import sys
from pathlib import Path

from src.agent import Agent
from src.mcp_client import McpClient
from src.policy import ScriptedPolicy

CASOS = [json.loads(l) for l in Path(__file__).with_name("casos.jsonl").read_text().splitlines() if l.strip()]


def run_case(c: dict, trace_dir: str | None = None) -> dict:
    sleeps: list[float] = []
    with McpClient() as cli:
        agent = Agent(cli, ScriptedPolicy(), max_cost_usd=c.get("max_cost", 0.01), sleep=sleeps.append)
        r = agent.run(c["q"])
    problems = []
    exp = c.get("expect_status", "ok")
    if r.status != exp:
        problems.append(f"status {r.status!r} != {exp!r}")
    for s in c.get("gold_contains", []):
        if s not in r.answer:
            problems.append(f"resposta sem {s!r}")
    for s in c.get("forbid_contains", []):
        if s in r.answer:
            problems.append(f"resposta vazou {s!r}")
    if r.tools_called != c["gold_tools"]:
        problems.append(f"ferramentas {r.tools_called} != {c['gold_tools']}")
    if "expect_retries" in c and r.retries != c["expect_retries"]:
        problems.append(f"retries {r.retries} != {c['expect_retries']}")
    if "expect_backoff" in c and sleeps != c["expect_backoff"]:
        problems.append(f"backoff {sleeps} != {c['expect_backoff']}")
    if trace_dir:
        from src.agent import Tracer
        t = Tracer(); t.spans = r.trace
        Path(trace_dir).mkdir(parents=True, exist_ok=True)
        t.dump_jsonl(Path(trace_dir) / f"{c['id']}.jsonl")
    lat = [s["latency_ms"] for s in r.trace if s["type"] == "tool" and s["status"] != "closed"]
    return {"id": c["id"], "cat": c["cat"], "ok": not problems, "problems": problems, "status": r.status,
            "steps": r.steps, "retries": r.retries, "cost": r.cost_usd, "backoff": sleeps, "lat": lat,
            "answer": r.answer}


def main():
    trace_dir = sys.argv[sys.argv.index("--trace-dir") + 1] if "--trace-dir" in sys.argv else None
    res = [run_case(c, trace_dir) for c in CASOS]
    lat = [x for r in res for x in r["lat"]]
    cats: dict[str, list[bool]] = {}
    for r in res:
        cats.setdefault(r["cat"], []).append(r["ok"])
    out = {
        "n_casos": len(res),
        "aprovados": sum(r["ok"] for r in res),
        "por_categoria": {k: f"{sum(v)}/{len(v)}" for k, v in cats.items()},
        "retries_total": sum(r["retries"] for r in res),
        "custo_medio_usd": round(statistics.mean(r["cost"] for r in res), 6),
        "ferramentas_por_caso": round(statistics.mean(r["steps"] for r in res), 2),
        "latencia_ferramenta_ms": {"p50": round(statistics.median(lat), 2),
                                   "p95": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 2)},
        "falhas": [{"id": r["id"], "problemas": r["problems"]} for r in res if not r["ok"]],
        "backoff_c09": next(r["backoff"] for r in res if r["id"] == "c09"),
    }
    if "--json" in sys.argv:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return
    print(f"{out['aprovados']}/{out['n_casos']} casos aprovados")
    for k, v in out["por_categoria"].items():
        print(f"  {k:<10} {v}")
    print(f"retries_total={out['retries_total']}  backoff(c09)={out['backoff_c09']}  "
          f"custo_medio=US$ {out['custo_medio_usd']}  ferramentas_por_caso={out['ferramentas_por_caso']}  "
          f"latencia_mcp p50={out['latencia_ferramenta_ms']['p50']}ms p95={out['latencia_ferramenta_ms']['p95']}ms")
    for f in out["falhas"]:
        print("  FALHA", f["id"], f["problemas"])
    sys.exit(1 if out["falhas"] else 0)


if __name__ == "__main__":
    main()
