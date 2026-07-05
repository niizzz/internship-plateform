"""Start uvicorn with the Proactor event loop policy on Windows.

Playwright needs subprocess support; only ProactorEventLoop provides that.
Setting the policy BEFORE importing uvicorn guarantees the server's loop is
the right type.
"""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    import os
    # Allow opting out of reload (recommended when agents are creating files
    # rapidly — reload thrashes and never settles).
    reload = os.environ.get("DEV_RELOAD", "0") == "1"
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=reload,
        loop="asyncio",
    )
