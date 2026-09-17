"""Vani - Real-Time AI Telephony Customer Care Platform."""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.health_routes import router as health_router
from src.api.twilio_routes import router as twilio_router
from src.api.privacy_routes import router as privacy_router
from src.config import settings
from src.utils.logger import get_logger

logger = get_logger("vani.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle event handler for application startup and shutdown."""
    logger.info(f"Initializing Vani Voice AI Platform in {settings.ENVIRONMENT} mode...")
    logger.info(f"Target Latency Budget: < {settings.MAX_LATENCY_THRESHOLD_MS}ms")
    yield
    logger.info("Vani Voice AI Platform shutting down cleanly.")


app = FastAPI(
    title="Vani - AI Voice Telephony Platform",
    description="Real-time sub-second voice customer care bot with multi-tenant telephony support.",
    version="0.1.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routers
app.include_router(health_router)
app.include_router(twilio_router)
app.include_router(privacy_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=(settings.ENVIRONMENT == "development")
    )
