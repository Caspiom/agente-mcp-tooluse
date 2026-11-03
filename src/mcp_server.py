"""Servidor MCP minimo: JSON-RPC 2.0 sobre stdio, uma mensagem JSON por linha.

Implementa o subconjunto do Model Context Protocol que um agente precisa:
  initialize -> (notifications/initialized) -> tools/list -> tools/call (+ ping)

Cinco ferramentas, todas deterministicas e offline:
  calculator, unit_convert, currency_convert, inventory_check (flaky de proposito), kb_search

Erros de ferramenta voltam como resultado `isError: true` com prefixo no texto:
  TRANSIENT:  vale tentar de novo (retry com backoff)
  INVALID: / NOT_FOUND:  permanente, NAO adianta repetir
Erros de protocolo voltam como erro JSON-RPC (-32700, -32601, -32602).
"""
from __future__ import annotations

import ast
import json
import operator
import re
import sys
import unicodedata

PROTOCOL_VERSION = "2025-06-18"


class ToolError(Exception):
    def __init__(self, kind: str, msg: str):
        super().__init__(msg)
        self.kind, self.msg = kind, msg


# ---------------------------------------------------------------- calculator
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_UN = {ast.USub: operator.neg, ast.UAdd: operator.pos}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        return _BIN[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
        return _UN[type(node.op)](_eval(node.operand))
    # qualquer outra coisa (nome, chamada, atributo, **, lambda...) e recusada
    raise ToolError("INVALID", "expressao nao permitida")


def _fmt(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.4f}".rstrip("0").rstrip(".")


def calculator(expression: str) -> str:
    expression = str(expression)
    if len(expression) > 100:
        raise ToolError("INVALID", "expressao longa demais")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        return _fmt(_eval(tree))
    except ToolError:
        raise
    except ZeroDivisionError:
        raise ToolError("INVALID", "divisao por zero")
    except (SyntaxError, ValueError, TypeError, RecursionError):
        raise ToolError("INVALID", "expressao nao permitida")


# -------------------------------------------------------------- conversoes
_UNIT_FACTORS = {("km", "mi"): 0.621371, ("kg", "lb"): 2.20462}


def unit_convert(value, from_unit: str, to_unit: str) -> str:
    v, a, b = _num(value), str(from_unit).lower(), str(to_unit).lower()
    if (a, b) in _UNIT_FACTORS:
        r = v * _UNIT_FACTORS[(a, b)]
    elif (b, a) in _UNIT_FACTORS:
        r = v / _UNIT_FACTORS[(b, a)]
    elif (a, b) == ("c", "f"):
        r = v * 9 / 5 + 32
    elif (a, b) == ("f", "c"):
        r = (v - 32) * 5 / 9
    else:
        raise ToolError("INVALID", f"conversao {a}->{b} nao suportada")
    return f"{r:.2f} {b}"


_RATES = {("USD", "BRL"): 5.0, ("EUR", "BRL"): 6.0, ("USD", "EUR"): 0.9}  # fixture fixa


def currency_convert(amount, from_currency: str, to_currency: str) -> str:
    v, a, b = _num(amount), str(from_currency).upper(), str(to_currency).upper()
    if a == b:
        r = v
    elif (a, b) in _RATES:
        r = v * _RATES[(a, b)]
    elif (b, a) in _RATES:
        r = v / _RATES[(b, a)]
    else:
        raise ToolError("INVALID", f"par {a}/{b} nao suportado")
    return f"{r:.2f} {b}"


def _num(x) -> float:
    try:
        return float(str(x).replace(",", "."))
    except ValueError:
        raise ToolError("INVALID", "valor numerico invalido")


# ------------------------------------------------------------- inventario
_STOCK = {"SKU-1": 12, "SKU-2": 0, "SKU-3": 7}
_FLAKY_FAILS = {"SKU-2": 2}   # falha as 2 primeiras chamadas e depois responde
_ALWAYS_DOWN = {"SKU-3"}      # backend fora do ar: sempre TRANSIENT
_calls: dict[str, int] = {}


def inventory_check(sku: str) -> str:
    sku = str(sku).upper()
    _calls[sku] = _calls.get(sku, 0) + 1
    if sku in _ALWAYS_DOWN:
        raise ToolError("TRANSIENT", "servico de estoque indisponivel (503)")
    if _calls[sku] <= _FLAKY_FAILS.get(sku, 0):
        raise ToolError("TRANSIENT", "timeout no servico de estoque")
    if sku not in _STOCK:
        raise ToolError("NOT_FOUND", f"item {sku} nao existe")
    return f"{sku}: {_STOCK[sku]} unidades"


# -------------------------------------------------------------------- KB
_DOCS = [
    ("politica-reembolso", "O reembolso de despesas de viagem e feito em ate 10 dias uteis apos a prestacao de contas."),
    ("politica-ferias", "Todo colaborador tem 30 dias corridos de ferias por ano."),
    ("politica-senha", "A senha corporativa deve ter no minimo 14 caracteres."),
]
_STOP = {"a", "o", "as", "os", "de", "da", "do", "e", "em", "para", "qual", "politica", "que", "um", "uma"}


def _toks(s: str) -> set[str]:
    s = "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))
    return {t for t in re.findall(r"[a-z0-9]+", s) if t not in _STOP}


def kb_search(query: str) -> str:
    q = _toks(str(query))
    best, score = None, 0
    for doc_id, text in _DOCS:
        s = len(q & (_toks(text) | _toks(doc_id)))
        if s > score:
            best, score = (doc_id, text), s
    if not best:
        raise ToolError("NOT_FOUND", "nada encontrado na base")
    return f"[{best[0]}] {best[1]}"


TOOLS = {
    "calculator": (calculator, "Avalia uma expressao aritmetica (+ - * /, parenteses).",
                   {"expression": {"type": "string"}}),
    "unit_convert": (unit_convert, "Converte unidades: km<->mi, kg<->lb, C<->F.",
                     {"value": {"type": "number"}, "from_unit": {"type": "string"}, "to_unit": {"type": "string"}}),
    "currency_convert": (currency_convert, "Converte moeda (cotacao fixa de fixture): USD, EUR, BRL.",
                         {"amount": {"type": "number"}, "from_currency": {"type": "string"}, "to_currency": {"type": "string"}}),
    "inventory_check": (inventory_check, "Consulta o estoque de um SKU (backend instavel).",
                        {"sku": {"type": "string"}}),
    "kb_search": (kb_search, "Busca uma politica interna na base de conhecimento.",
                  {"query": {"type": "string"}}),
}


def _tool_list():
    return [{"name": n, "description": d,
             "inputSchema": {"type": "object", "properties": props, "required": list(props)}}
            for n, (_, d, props) in TOOLS.items()]


# --------------------------------------------------------------- protocolo
def handle(msg: dict) -> dict | None:
    """Processa uma mensagem JSON-RPC. Devolve a resposta, ou None p/ notificacao."""
    mid, method = msg.get("id"), msg.get("method")
    is_notification = "id" not in msg

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if is_notification:
        return None  # ex.: notifications/initialized
    if method == "initialize":
        return ok({"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                   "serverInfo": {"name": "portfolio-tools", "version": "0.1.0"}})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": _tool_list()})
    if method == "tools/call":
        p = msg.get("params") or {}
        name, args = p.get("name"), p.get("arguments") or {}
        if name not in TOOLS:
            return err(-32602, f"ferramenta desconhecida: {name}")
        try:
            text, is_error = TOOLS[name][0](**args), False
        except ToolError as e:
            text, is_error = f"{e.kind}: {e.msg}", True
        except TypeError:
            text, is_error = "INVALID: argumentos incorretos", True
        return ok({"content": [{"type": "text", "text": text}], "isError": is_error})
    return err(-32601, f"metodo desconhecido: {method}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if not isinstance(msg, dict):
                raise ValueError
            resp = handle(msg)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON invalido"}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
