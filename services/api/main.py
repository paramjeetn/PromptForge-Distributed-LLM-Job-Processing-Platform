import os

import sentry_sdk
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN", ""),
    traces_sample_rate=0.1,
)

app = FastAPI(title="PromptForge API", version="1.0.0")

from routes.jobs import router as jobs_router
app.include_router(jobs_router, prefix="/v1")


@app.get("/healthz")
def health():
    return {"status": "ok"}
