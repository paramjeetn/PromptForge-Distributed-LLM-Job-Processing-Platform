import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

from shared.observability import setup as setup_observability
setup_observability("api")

app = FastAPI(title="PromptForge API", version="1.0.0")

from routes.jobs import router as jobs_router
from routes.status import router as status_router
from routes.results import router as results_router

app.include_router(jobs_router, prefix="/v1")
app.include_router(status_router, prefix="/v1")
app.include_router(results_router, prefix="/v1")


@app.get("/healthz")
def health():
    return {"status": "ok"}
