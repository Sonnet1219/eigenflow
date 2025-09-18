"""Main FastAPI application for ArXiv Scraper backend."""

import os
import sys
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Fix for Windows event loop policy
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from src.db.checkpoints import CheckpointerManager
from src.agent.graph import build_graph
from src.api.graph import router as graph_router
from src.api.models import ErrorResponse

import logging
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage the application's lifespan. This is the recommended way to manage
    resources that need to be initialized on startup and cleaned up on shutdown.
    """
    # Get the database URL from environment variables
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is not set")

    try:
        # Initialize the async checkpointer
        await CheckpointerManager.initialize(DATABASE_URL)
        logger.info("Async checkpointer initialized successfully")
        
        # Initialize database connection and create tables if needed
        from src.db.database import DatabaseManager
        from src.agent.utils import create_schema_if_not_exists
        
        await DatabaseManager.initialize(DATABASE_URL)
        pool = await DatabaseManager.get_pool()
        
        # Check and create database tables on startup
        try:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    logger.info("Checking database schema on startup...")
                    await create_schema_if_not_exists(cur)
                    logger.info("Database schema check completed successfully")
        except Exception as e:
            logger.error(f"Failed to check/create database schema: {str(e)}")
            # Don't raise here, as the app might still work with existing tables
            # But log the error for debugging
        
        # Get the checkpointer instance
        checkpointer = await CheckpointerManager.get_checkpointer()

        # Build the graph with async checkpointer
        graph = await build_graph(checkpointer)
        app.state.graph = graph
        
        # Build the data processing graph with async checkpointer
        logger.info("Successfully compiled graphs and attached to app state.")
        
        yield
        
    except Exception as e:
        logger.error(f"Failed to initialize application: {str(e)}")
        raise
    finally:
        # Clean up resources on shutdown
        await CheckpointerManager.close()
    logger.info("Application shutdown: graph resources released.")


# Create FastAPI app
app = FastAPI(
    title="ArXiv Scraper API",
    description="Backend API for ArXiv scraping and minimal chat",
    version="0.1.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(graph_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler for unhandled exceptions."""
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            code=500,
            message="Internal Server Error",
            detail=str(exc),
        ).model_dump(),
    )


@app.get("/")
async def root():
    """Root endpoint to check if API is running."""
    return {"message": "ArXiv Scraper API is running"} 