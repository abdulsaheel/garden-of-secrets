"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings, STATIC_DIR
from app.database import init_db, close_db
from app.s3 import S3Service

settings = get_settings()

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("vault")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Vault v%s", settings.app_version)
    await init_db()
    logger.info("Database tables ensured")
    try:
        await S3Service.ensure_bucket()
        logger.info("S3 bucket '%s' ready at %s", settings.s3_bucket, settings.s3_endpoint_url)
    except Exception as e:
        logger.warning("Could not verify S3 bucket: %s", e)
    yield
    await close_db()
    logger.info("Vault shut down")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

# Middleware
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)

# Static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Routers
from app.routers import auth, files, change_requests, admin, sharing, pages  # noqa: E402

app.include_router(auth.router)
app.include_router(files.router)
app.include_router(change_requests.router)
app.include_router(admin.router)
app.include_router(sharing.router)
app.include_router(pages.router)


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# Health check
@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.app_version}
