import logging
import os

import uvicorn

from src.api import app  # noqa: F401

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

uvicorn.run(
    app,
    host="0.0.0.0",
    port=int(os.environ.get("LISTEN_PORT", "7654")),
    log_config=None,
)
