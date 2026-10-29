from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import initialize_database
from app.routes.dashboard import router as dashboard_router
from app.routes.messages import router as messages_router
from app.routes.results import router as results_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="Ticket Intelligence System",
    description="Customer ticket processing and classification API",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for frontend/dashboard access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(messages_router)
app.include_router(dashboard_router)
app.include_router(results_router)


@app.get("/")
def root():
    return {
        "service": "Ticket Intelligence System",
        "status": "running",
    }
