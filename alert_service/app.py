"""Alert Service FastAPI Application."""

import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from alert_service.api import router as alert_router

# Fix for Windows event loop policy
import sys
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Create FastAPI app
app = FastAPI(
    title="Alert Service API",
    description="Dedicated service for LP margin monitoring and alerting",
    version="0.1.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(alert_router)

@app.get("/")
async def root():
    """Root endpoint to check if Alert Service is running."""
    return {"message": "Alert Service is running", "port": 8002}

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "alert-service"}
