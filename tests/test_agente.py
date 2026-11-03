import json
import subprocess
import sys

import pytest

from src import mcp_server as S
from src.agent import Agent
from src.mcp_client import ROOT, McpClient, McpProtocolError, McpTimeout
from src.policy import Action, Final, ScriptedPolicy


@pytest.fixture
def cli():
    with McpClient() as c:
        yield c


class FakeClient:
    """Cliente falso p/ testar o agente sem subprocesso: devolve uma sequencia de respostas."""
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def call_tool(self, name, args):
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


# ------------------------------------------------------------- protocolo MCP
def test_handshake_e_lista_de_ferramentas(cli):
    names = {t["name"] for t in cli.list_tools()}
    assert names == {"calculator", "unit_convert", "currency_convert", "inventory_check", "kb_search"}
    assert all(t["inputSchema"]["type"] == "object" for t in cli.list_tools())


def test_initialize_anuncia_versao_e_capacidade_de_tools(cli):
    r = S.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r["result"]["protocolVersion"] == S.PROTOCOL_VERSION and "tools" in r["result"]["capabilities"]


def test_notificacao_nao_gera_resposta():
    assert S.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_ferramenta_desconhecida_e_erro_jsonrpc(cli):
    with pytest.raises(McpProtocolError, match="-32602"):
        cli.call_tool("nao_existe", {})


def test_metodo_desconhecido_e_erro_jsonrpc(cli):
    with pytest.raises(McpProtocolError, match="-32601"):
        cli.request("foo/bar")


def test_json_invalido_no_stdin_nao_derruba_o_servidor():
    p = subprocess.Popen([sys.executable, "-m", "src.mcp_server"], cwd=ROOT, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True)
    out, _ = p.communicate("{isso nao e json\n" + json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n", timeout=5)
    l1, l2 = [json.loads(x) for x in out.strip().splitlines()]
    assert l1["error"]["code"] == -32700 and l2["id"] == 7 and l2["result"] == {}


# ------------------------------------------------------------- ferramentas
def test_calculadora_basica(cli):
    assert cli.call_tool("calculator", {"expression": "12*8+4"}) == ("100", False)
    assert cli.call_tool("calculator", {"expression": "(100-20)/4"}) == ("20", False)


@pytest.mark.parametrize("expr", ["__import__('os').getcwd()", "open('/etc/passwd')", "().__class__",
                                  "__import__('os').getpid()", "abs(-1)", "len('abc')",  # injecao com resultado NUMERICO
                                  "9**9**9", "lambda: 1", "[1]*10**9", "x+1", "1/0", "1;2", "a" * 200])
def test_calculadora_recusa_expressao_perigosa(cli, expr):
    text, is_err = cli.call_tool("calculator", {"expression": expr})
    assert is_err and text.startswith("INVALID")


def test_conversoes_e_cambio(cli):
    assert cli.call_tool("unit_convert", {"value": 10, "from_unit": "km", "to_unit": "mi"}) == ("6.21 mi", False)
    assert cli.call_tool("unit_convert", {"value": 100, "from_unit": "c", "to_unit": "f"}) == ("212.00 f", False)
    assert cli.call_tool("currency_convert", {"amount": 100, "from_currency": "BRL", "to_currency": "USD"}) == ("20.00 USD", False)


def test_estoque_instavel_falha_duas_vezes_e_depois_responde(cli):
    r = [cli.call_tool("inventory_check", {"sku": "SKU-2"}) for _ in range(3)]
    assert [x[1] for x in r] == [True, True, False]
    assert r[0][0].startswith("TRANSIENT") and r[2][0] == "SKU-2: 0 unidades"


def test_estoque_inexistente_e_erro_permanente(cli):
    text, is_err = cli.call_tool("inventory_check", {"sku": "SKU-404"})
    assert is_err and text.startswith("NOT_FOUND")


def test_argumentos_errados_viram_erro_de_ferramenta_nao_crash(cli):
    text, is_err = cli.call_tool("calculator", {"foo": "bar"})
    assert is_err and text.startswith("INVALID")
    assert cli.call_tool("calculator", {"expression": "1+1"}) == ("2", False)  # servidor continua vivo


# ------------------------------------------------------------------ agente
def _agent(client, **kw):
    sleeps = []
    return Agent(client, ScriptedPolicy(), sleep=sleeps.append, **kw), sleeps


def test_retry_com_backoff_exponencial_e_recupera():
    a, sleeps = _agent(FakeClient([("TRANSIENT: x", True), ("TRANSIENT: x", True), ("SKU-1: 5 unidades", False)]))
    r = a.run("Qual o estoque do item SKU-1?")
    assert r.status == "ok" and r.retries == 2 and sleeps == [0.2, 0.4]


def test_timeout_do_mcp_conta_como_transitorio():
    a, sleeps = _agent(FakeClient([McpTimeout("t"), ("SKU-1: 5 unidades", False)]))
    r = a.run("Qual o estoque do item SKU-1?")
    assert r.status == "ok" and r.retries == 1 and sleeps == [0.2]


def test_retry_esgota_e_desiste_sem_loop_infinito():
    fc = FakeClient([("TRANSIENT: x", True)] * 10)
    a, sleeps = _agent(fc)
    r = a.run("Qual o estoque do item SKU-1?")
    assert r.status == "tool_failed" and fc.calls == 3 and sleeps == [0.2, 0.4]


def test_erro_permanente_nao_e_repetido():
    fc = FakeClient([("NOT_FOUND: x", True)] * 5)
    a, sleeps = _agent(fc)
    r = a.run("Qual o estoque do item SKU-9?")
    assert r.status == "tool_error" and fc.calls == 1 and sleeps == []


def test_teto_de_custo_bloqueia_a_acao_antes_de_executar():
    fc = FakeClient([("96", False), ("480.00 BRL", False)])
    a, _ = _agent(fc, max_cost_usd=0.0004, max_output_tokens=20)
    r = a.run("Calcule 12*8 e depois converta o resultado de USD para BRL")
    assert r.status == "budget_exceeded" and fc.calls == 1  # 2a ferramenta NAO foi chamada


class CountingPolicy:
    """Politica que conta quantas vezes o 'modelo' foi chamado."""
    def __init__(self, inner):
        self.inner, self.calls = inner, 0

    def decide(self, q, scratch):
        self.calls += 1
        return self.inner.decide(q, scratch)


def test_teto_duro_custo_final_nunca_passa_do_teto_e_nao_chama_o_modelo_que_estouraria():
    pol = CountingPolicy(ScriptedPolicy())
    fc = FakeClient([("96", False), ("480.00 BRL", False)])
    r = Agent(fc, pol, max_cost_usd=0.0004, max_output_tokens=20, sleep=lambda s: None).run(
        "Calcule 12*8 e depois converta o resultado de USD para BRL")
    assert r.status == "budget_exceeded" and r.cost_usd <= 0.0004
    assert pol.calls == 1                        # o 2o passo foi barrado ANTES de pagar o modelo
    assert r.trace[-1]["type"] == "budget_block" and r.trace[-1]["est_pior_caso_usd"] > 0.0004 - r.cost_usd


def test_teto_menor_que_um_passo_nao_chama_o_modelo_nem_gasta_nada():
    pol = CountingPolicy(ScriptedPolicy())
    fc = FakeClient([])
    r = Agent(fc, pol, max_cost_usd=0.0001, max_output_tokens=20, sleep=lambda s: None).run("Calcule 1+1")
    assert r.status == "budget_exceeded" and pol.calls == 0 and fc.calls == 0 and r.cost_usd == 0


def test_pior_caso_cresce_com_o_historico_e_com_max_output_tokens():
    a = Agent(FakeClient([]), ScriptedPolicy(), max_output_tokens=100)
    b = Agent(FakeClient([]), ScriptedPolicy(), max_output_tokens=200)
    from src.policy import Step
    hist = [Step(Action("calculator", {"expression": "1+1"}), "2" * 400)]
    assert a.worst_case_usd("oi", hist) > a.worst_case_usd("oi", []) and b.worst_case_usd("oi", []) > a.worst_case_usd("oi", [])


def test_safety_margin_deixa_o_estimador_mais_conservador():
    base = Agent(FakeClient([]), ScriptedPolicy()).worst_case_usd("oi", [])
    assert Agent(FakeClient([]), ScriptedPolicy(), safety_margin=1.5).worst_case_usd("oi", []) == pytest.approx(base * 1.5)


class Verbose:
    """Politica que IGNORA max_output_tokens (o que um provedor impediria com max_tokens)."""
    def decide(self, q, scratch):
        return Final("x" * 400)


def test_limite_conhecido_politica_que_ignora_max_tokens_ainda_e_pega_depois_da_chamada():
    r = Agent(FakeClient([]), Verbose(), max_cost_usd=0.0004, max_output_tokens=20, sleep=lambda s: None).run("oi")
    assert r.status == "budget_exceeded" and r.cost_usd > 0.0004   # documentado: so vale se max_tokens for respeitado


def test_encadeamento_usa_a_observacao_anterior(cli):
    a, _ = _agent(cli)
    r = a.run("Calcule 3*50 e depois converta o resultado de EUR para BRL")
    assert r.answer == "150 EUR = 900.00 BRL." and r.tools_called == ["calculator", "currency_convert"]


class LoopPolicy:
    def decide(self, q, scratch):
        return Action("calculator", {"expression": "1+1"})


class ForeverDifferent:
    def decide(self, q, scratch):
        return Action("calculator", {"expression": f"1+{len(scratch)}"})


def test_detecta_loop_de_acao_repetida(cli):
    r = Agent(cli, LoopPolicy(), sleep=lambda s: None).run("x")
    assert r.status == "loop_detected" and r.tools_called == ["calculator"]


def test_limite_de_passos(cli):
    r = Agent(cli, ForeverDifferent(), max_steps=3, max_cost_usd=1.0, sleep=lambda s: None).run("x")
    assert r.status == "max_steps" and len(r.tools_called) == 3


def test_servidor_morto_vira_tool_failed_sem_retry():
    c = McpClient()
    c.start()
    try:
        c.proc.kill(); c.proc.wait()
        r = Agent(c, ScriptedPolicy(), sleep=lambda s: None).run("Calcule 1+1")
    finally:
        c.close()
    assert r.status == "tool_failed"


# ----------------------------------------------------------------- tracing
def test_trace_tem_spans_de_politica_ferramenta_e_backoff(cli, tmp_path):
    from src.agent import Tracer
    a, _ = _agent(cli)
    r = a.run("Qual o estoque do item SKU-2?")
    kinds = [s["type"] for s in r.trace]
    assert kinds == ["policy", "tool", "backoff", "tool", "backoff", "tool", "policy"]
    tools = [s for s in r.trace if s["type"] == "tool"]
    assert [t["attempt"] for t in tools] == [1, 2, 3] and all("latency_ms" in t for t in tools)
    assert len({s["trace_id"] for s in r.trace}) == 1
    t = Tracer(); t.spans = r.trace
    p = tmp_path / "t.jsonl"; t.dump_jsonl(p)
    assert [json.loads(l)["span_id"] for l in p.read_text().splitlines()] == list(range(1, 8))
