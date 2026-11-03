"""Cliente MCP (stdio). Sobe o servidor como subprocesso, faz o handshake e chama ferramentas.

Uma thread leitora joga as linhas do stdout numa fila; assim `request` tem TIMEOUT de verdade
(readline direto bloquearia para sempre se o servidor travasse)."""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class McpTimeout(Exception):
    pass


class McpClosed(Exception):
    pass


class McpProtocolError(Exception):
    pass


class McpClient:
    def __init__(self, cmd: list[str] | None = None, timeout: float = 5.0):
        self.cmd = cmd or [sys.executable, "-m", "src.mcp_server"]
        self.timeout = timeout
        self._id = 0
        self._q: queue.Queue = queue.Queue()
        self.proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None

    # -- ciclo de vida
    def start(self) -> dict:
        self.proc = subprocess.Popen(self.cmd, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, bufsize=0)  # binario sem buffer: escrita em servidor morto falha na hora, sem resíduo
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        info = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                           "clientInfo": {"name": "agente-portfolio", "version": "0.1.0"}})
        self.notify("notifications/initialized")
        return info

    def close(self) -> None:
        """Encerra o servidor e fecha os pipes (sem isso: ResourceWarning e thread leitora pendurada)."""
        p = self.proc
        if p is None:
            return
        try:
            p.stdin.close()
        except Exception:
            pass
        try:
            p.wait(timeout=2)
        except Exception:
            p.kill()
            p.wait()
        if self._thread:
            self._thread.join(timeout=2)
        try:
            p.stdout.close()
        except Exception:
            pass

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    def _reader(self) -> None:
        try:
            for line in iter(self.proc.stdout.readline, b""):
                self._q.put(line.decode("utf-8"))
        except (ValueError, OSError):
            pass  # pipe fechado (servidor morto/encerrado): tratado como EOF
        self._q.put(None)  # EOF: servidor morreu

    # -- JSON-RPC
    def _send(self, obj: dict) -> None:
        try:
            self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        except (BrokenPipeError, ValueError):
            raise McpClosed("servidor MCP fechou")

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        while True:
            try:
                line = self._q.get(timeout=self.timeout)
            except queue.Empty:
                raise McpTimeout(f"sem resposta de {method} em {self.timeout}s")
            if line is None:
                raise McpClosed("servidor MCP encerrou")
            msg = json.loads(line)
            if msg.get("id") != rid:
                continue
            if "error" in msg:
                raise McpProtocolError(f'{msg["error"]["code"]}: {msg["error"]["message"]}')
            return msg["result"]

    # -- API de alto nivel
    def list_tools(self) -> list[dict]:
        return self.request("tools/list")["tools"]

    def call_tool(self, name: str, args: dict) -> tuple[str, bool]:
        """Devolve (texto, is_error)."""
        r = self.request("tools/call", {"name": name, "arguments": args})
        return "".join(c.get("text", "") for c in r["content"]), bool(r.get("isError"))
