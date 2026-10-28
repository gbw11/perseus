"""Optional guest-bridge listener exposing ingestion only, never VM control/read APIs."""
import os

from fastapi import FastAPI

from .api.logs import ingest, ERRORS
from .api.errors import install_error_handlers
from .config import Settings


def create_app(settings=None):
    app = FastAPI(title="Private runtime log ingestion", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings or Settings.from_env()
    app.add_api_route("/api/v1/logs/ingest", ingest, methods=["POST"], responses=ERRORS)
    install_error_handlers(app)
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host=os.environ.get("XENOVA_LOG_INGEST_HOST", "127.0.0.1"),
                port=int(os.environ.get("XENOVA_LOG_INGEST_PORT", "8084")), access_log=False)
