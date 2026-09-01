from __future__ import annotations

import argparse
import os
import threading
import webbrowser
from collections.abc import Sequence
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .api.chat import router as chat_router
from .api.map import router as map_router
from .api.status import router as status_router
from .api.workspace import router as workspace_router
from .config import Settings
from .engine import Runtime


def create_application(settings: Settings | None = None) -> FastAPI:
    runtime = Runtime.create(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    application = FastAPI(
        title="Satisfactory Helper",
        summary="A local, snapshot-only Satisfactory factory planning workbench.",
        lifespan=lifespan,
    )
    application.state.runtime = runtime
    application.include_router(chat_router)
    application.include_router(map_router)
    application.include_router(status_router)
    application.include_router(workspace_router)
    application.mount("/engine", runtime.engine_app)

    index = runtime.settings.web_dist / "index.html"
    if index.is_file():
        application.mount(
            "/", StaticFiles(directory=runtime.settings.web_dist, html=True), name="web"
        )
    else:

        @application.get("/", include_in_schema=False)
        async def frontend_missing() -> PlainTextResponse:
            return PlainTextResponse(
                "Frontend not built. Run `pnpm --dir apps/web build`, or use `pnpm dev`.\n",
                status_code=503,
            )
    return application


app = create_application()


def cli(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run Satisfactory Helper on localhost")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=app.state.runtime.settings.port)
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if os.environ.get("SATISFACTORY_HELPER_BUNDLED") == "1" and not args.no_browser:
        url = f"http://{args.host}:{args.port}"
        threading.Timer(1.1, webbrowser.open, args=(url,)).start()
    uvicorn.run(
        "satisfactory_helper.main:app" if args.dev else app,
        host=args.host,
        port=args.port,
        reload=args.dev,
        access_log=args.dev,
    )


if __name__ == "__main__":
    cli()
