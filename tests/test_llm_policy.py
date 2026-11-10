import json

import pytest

from src.agent import Agent
from src.llm_policy import LlmPolicy, extract_json
from src.mcp_client import McpClient
from src.policy import Action, Final, ScriptedPolicy, Step


class ModeloFalso:
    def __init__(self, *respostas):
        self.respostas, self.prompts = list(respostas), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.respostas.pop(0) if len(self.respostas) > 1 else self.respostas[0]


class ClienteContador:
    def __init__(self):
        self.chamadas = 0

    def call_tool(self, nome, args):
        self.chamadas += 1
        return "42", False


@pytest.fixture
def ferramentas():
    with McpClient() as c:
        yield c.list_tools()


CALC = '{"tool": "calculator", "args": {"expression": "2+3*4"}}'


@pytest.mark.parametrize("bruto", [
    CALC,
    "```json\n" + CALC + "\n```",
    "Claro! Vou usar a calculadora:\n" + CALC + "\nEspero ter ajudado.",
])
def test_extract_json_aceita_json_puro_cercado_e_com_texto_em_volta(bruto):
    assert extract_json(bruto)["tool"] == "calculator"


@pytest.mark.parametrize("bruto", ["", "sem json aqui", "{quebrado", "[1, 2]", "{\"tool\": }"])
def test_extract_json_rejeita_o_que_nao_e_objeto_valido(bruto):
    with pytest.raises(ValueError):
        extract_json(bruto)


def test_decide_devolve_acao_e_final(ferramentas):
    politica = LlmPolicy(ModeloFalso(CALC), ferramentas)
    acao = politica.decide("quanto e 2+3*4?", [])
    assert isinstance(acao, Action) and acao.tool == "calculator" and acao.args == {"expression": "2+3*4"}
    final = LlmPolicy(ModeloFalso('{"final": "14"}'), ferramentas).decide("q", [])
    assert isinstance(final, Final) and final.text == "14" and final.status == "ok"


@pytest.mark.parametrize("bruto", [
    "nao sei",
    '{"tool": "apagar_tudo", "args": {}}',
    '{"tool": "calculator", "args": "2+2"}',
    '{"args": {}}',
    '{"final": 42}',
])
def test_saida_ruim_do_modelo_vira_policy_error_sem_chamar_ferramenta(ferramentas, bruto):
    cliente = ClienteContador()
    r = Agent(cliente, LlmPolicy(ModeloFalso(bruto), ferramentas)).run("quanto e 2+2?")
    assert r.status == "policy_error" and cliente.chamadas == 0 and r.tools_called == []


def test_prompt_traz_as_ferramentas_e_o_historico(ferramentas):
    politica = LlmPolicy(ModeloFalso('{"final": "ok"}'), ferramentas)
    historico = [Step(Action("calculator", {"expression": "1+1"}), "2")]
    prompt = politica.build_prompt("pergunta de teste", historico)
    assert "pergunta de teste" in prompt and "Observation: 2" in prompt
    assert all(t["name"] in prompt for t in ferramentas)


def test_ponta_a_ponta_com_o_servidor_mcp_de_verdade(ferramentas):
    modelo = ModeloFalso(CALC, '{"final": "O resultado e 14."}')
    with McpClient() as cli:
        r = Agent(cli, LlmPolicy(modelo, ferramentas)).run("quanto e 2+3*4?")
    assert r.status == "ok" and r.answer == "O resultado e 14." and r.tools_called == ["calculator"]
    assert "Observation: 14" in modelo.prompts[1]


def test_modelo_que_repete_a_mesma_acao_e_barrado_por_loop(ferramentas):
    with McpClient() as cli:
        r = Agent(cli, LlmPolicy(ModeloFalso(CALC), ferramentas)).run("quanto e 2+3*4?")
    assert r.status == "loop_detected" and r.tools_called == ["calculator"]


def test_custo_conta_o_prompt_inteiro_e_barra_antes_de_chamar_o_modelo(ferramentas):
    pergunta, teto = "Calcule 1+1", 0.0008
    modelo = ModeloFalso('{"final": "2"}')
    llm = Agent(ClienteContador(), LlmPolicy(modelo, ferramentas), max_cost_usd=teto, max_output_tokens=32)
    regras = Agent(ClienteContador(), ScriptedPolicy(), max_cost_usd=teto, max_output_tokens=32)
    assert regras.worst_case_usd(pergunta, []) < teto < llm.worst_case_usd(pergunta, [])
    assert regras.run(pergunta).status == "ok"
    r = llm.run(pergunta)
    assert r.status == "budget_exceeded" and r.cost_usd == 0 and modelo.prompts == []


def test_custo_usa_o_texto_bruto_do_modelo_e_nao_a_acao_reserializada(ferramentas):
    curto = ModeloFalso('{"final": "2"}')
    longo = ModeloFalso('{"final": "2"}' + " " * 2000)
    custo = lambda modelo: Agent(ClienteContador(), LlmPolicy(modelo, ferramentas), max_cost_usd=1.0).run("Calcule 1+1").cost_usd
    assert custo(longo) > custo(curto) + 0.007


def test_saida_invalida_tambem_e_cobrada_pelo_texto_bruto(ferramentas):
    lixo = "blablabla " * 200
    r = Agent(ClienteContador(), LlmPolicy(ModeloFalso(lixo), ferramentas), max_cost_usd=1.0).run("q")
    assert r.status == "policy_error" and r.cost_usd > 0.007
