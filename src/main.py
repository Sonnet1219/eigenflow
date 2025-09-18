"""Main entry point for EigenFlow service."""

import uvicorn
import os
import sys
import logging
from dotenv import load_dotenv

# Configure logging early so module loggers (logger.info) are visible in terminal
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Load environment variables from .env file before any other imports
load_dotenv()

# Add project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if __name__ == "__main__":
    uvicorn.run(
        "src.api.app:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
    ) 