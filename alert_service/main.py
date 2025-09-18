"""Alert Service - Dedicated service for LP margin monitoring and alerting."""

import uvicorn
import os
import sys
import logging
from dotenv import load_dotenv

# Configure logging early
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Load environment variables
load_dotenv()

# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if __name__ == "__main__":
    uvicorn.run(
        "alert_service.app:app",
        host="0.0.0.0",
        port=8002,
        reload=True,
    )
