"""Interoperabilidade: o servidor deste repo fala MCP de verdade?

Conecta o cliente OFICIAL do SDK `mcp` ao nosso servidor (stdio), faz initialize, lista as
ferramentas e chama algumas. Se o SDK conseguir, o protocolo esta correto (nao so 'funciona
com o meu proprio cliente').

Nao faz parte do `make all` (o SDK e dependencia opcional, so p/ esta prova):
    python3 -m venv /tmp/mcp-interop && /tmp/mcp-interop/bin/pip install mcp
    /tmp/mcp-interop/bin/python scripts/interop_sdk.py"""
import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def g(obj, camel, snake):
    """SDK 1.x usa camelCase (serverInfo), 2.x usa snake_case (server_info)."""
    return getattr(obj, snake, None) if hasattr(obj, snake) else getattr(obj, camel)


def text(res):
    return res.content[0].text  # type: ignore[union-attr]


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=["-m", "src.mcp_server"], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            init = await s.initialize()
            info = g(init, "serverInfo", "server_info")
            print("servidor:", info.name, info.version, "| protocolo:", g(init, "protocolVersion", "protocol_version"))
            tools = await s.list_tools()
            print("ferramentas:", sorted(t.name for t in tools.tools))
            ok = await s.call_tool("calculator", {"expression": "12*8+4"})
            bad = await s.call_tool("calculator", {"expression": "__import__('os').getpid()"})
            ok_err, bad_err = g(ok, "isError", "is_error"), g(bad, "isError", "is_error")
            print("calculator 12*8+4 ->", text(ok), "| is_error =", ok_err)
            print("injecao ->", text(bad), "| is_error =", bad_err)
            assert text(ok) == "100" and not ok_err
            assert bad_err and text(bad).startswith("INVALID")
            assert len(tools.tools) == 5
    print("INTEROP OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
