from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import (
    initialize_database,
    check_database_health,
)
from app.routes.messages import router as messages_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="Ascentium Task",
    description="Customer ticket processing and classification API",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(messages_router)



@app.get("/")
def root():
    return {
        "service": "Ascentium Task",
        "status": "running",
    }


@app.get("/health")
def health():
    database_healthy = check_database_health()

    return {
        "status": "healthy" if database_healthy else "degraded",
        "services": {
            "database": (
                "healthy" if database_healthy else "unavailable"
            ),
            "llm": "not_configured",
        },
    }