"""Talks to bee-database over its MCP endpoint, with a service token. One short-lived session per call."""

from __future__ import annotations

import json
from typing import Any

import httpx2 as httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


class DbError(Exception):
    """The database refused or failed a call; the message is safe to show."""


class Db:
    def __init__(self, url: str, token: str) -> None:
        self.url, self.token = url, token

    async def call(self, tool: str, **args: Any) -> Any:
        args = {k: v for k, v in args.items() if v is not None}
        try:
            async with httpx.AsyncClient(headers={"Authorization": f"Bearer {self.token}"}, timeout=30) as hc:
                async with streamable_http_client(self.url, http_client=hc) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        res = await session.call_tool(tool, args)
        except Exception as e:  # connection refused, 401, protocol errors...
            raise DbError(f"The database didn't answer ({type(e).__name__}).") from e
        text = "".join(getattr(c, "text", "") for c in res.content)
        if res.is_error:
            raise DbError(text.split(": ", 1)[-1] if text.startswith("Error executing tool") else text or "Database error")
        if res.structured_content is not None:
            sc = res.structured_content
            return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
        try:
            return json.loads(text)
        except ValueError:
            return text
