"""
Utility functions and tools for the multi-agent system.

Contains helper functions for:
- Database schema creation  
- Chat functionality
- General utility functions
"""

import os
import logging
import asyncio
from langchain_core.tools import tool


logger = logging.getLogger(__name__)


@tool  
def chat_response(message: str) -> str:
    """Provide helpful chat responses for general questions and conversations."""
    # This could be enhanced to call external APIs or knowledge bases
    return f"I understand you're asking about: '{message}'. I'm here to help you with any questions or conversations you'd like to have. How can I assist you further?"


async def create_schema_if_not_exists(cur) -> None:
    """Create all tables and constraints per db_schema.md (simplified types with identity PK)."""
    await cur.execute(
        """
        CREATE TABLE IF NOT EXISTS margin_check (
            id BIGSERIAL PRIMARY KEY,
            tenant_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            margin FLOAT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
        """
    )