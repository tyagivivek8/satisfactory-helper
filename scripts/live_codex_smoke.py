"""Run one live, subscription-backed Codex query against the newest safe snapshot."""

from __future__ import annotations

import argparse
import asyncio

import httpx
from satisfactory_helper.codex import CodexRunner
from satisfactory_helper.engine import Runtime
from satisfactory_helper.models import ChatRequest


async def run(message: str, timeout: float) -> None:
    runtime = Runtime.create()
    if runtime.snapshot is None:
        raise RuntimeError("No safe save snapshot is available")
    transport = httpx.ASGITransport(app=runtime.engine_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://engine") as client:
        response = await client.get("/api/summary")
        response.raise_for_status()
        save_token = str(response.json()["save_token"])
    request = ChatRequest(message=message, save_token=save_token)
    runner = CodexRunner(runtime, timeout_seconds=timeout)
    async for part in runner.stream(
        request,
        save_token=save_token,
        snapshot_name=runtime.snapshot.source_name,
    ):
        print(part.decode().strip(), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("message")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    asyncio.run(run(args.message, args.timeout))


if __name__ == "__main__":
    main()
