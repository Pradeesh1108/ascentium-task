from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import PROJECT_DIR
from app.database import initialize_database
from app.routes.dashboard import router as dashboard_router
from app.routes.messages import router as messages_router
from app.routes.results import router as results_router

FRONTEND_DIR = PROJECT_DIR / "frontend"
FRONTEND_INDEX = FRONTEND_DIR / "index.html"


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


# Mount static files if frontend folder exists
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/", include_in_schema=False)
def root():
    """Serves the Kestrel Ticket Desk frontend UI if available, else API status."""
    if FRONTEND_INDEX.exists():
        return FileResponse(FRONTEND_INDEX)
    return {
        "service": "Ticket Intelligence System",
        "status": "running",
    }


@app.get("/ui", include_in_schema=False)
def serve_ui():
    """Direct route for Kestrel Ticket Desk UI."""
    if FRONTEND_INDEX.exists():
        return FileResponse(FRONTEND_INDEX)
    return {
        "service": "Ticket Intelligence System",
        "status": "running",
    }


@app.get("/api")
def api_info():
    """API metadata and status endpoint."""
    return {
        "service": "Ticket Intelligence System",
        "status": "running",
        "docs": "/docs",
    }
