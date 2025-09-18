"""checkpointer.py"""

from typing import Optional, cast, Any
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
import logging
from .database import DatabaseManager

logger = logging.getLogger(__name__)


class CheckpointerManager:
    """Workflow checkpoint manager"""

    _instance: Optional["CheckpointerManager"] = None
    _checkpointer: Optional[AsyncPostgresSaver] = None
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    async def initialize(cls, db_uri: str, max_size: int = 20) -> None:
        """
        Initialize the checkpoint manager

        Args:
            db_uri: database connection URI
            max_size: maximum number of connections in the connection pool
        """
        if cls._initialized and cls._checkpointer is not None:
            logger.debug("Checkpointer already initialized, skipping setup")
            return

        try:
            # Initialize the connection pool using DatabaseManager
            await DatabaseManager.initialize(db_uri, max_size)
            pool = await DatabaseManager.get_pool()

            # Initialize the checkpoint saver
            cls._checkpointer = AsyncPostgresSaver(cast(Any, pool))
            if not cls._initialized:
                # Run setup only once per process lifetime, but ignore benign duplicate migration errors
                try:
                    await cls._checkpointer.setup()
                except Exception as e:
                    msg = str(e).lower()
                    if "already exists" in msg or "duplicate" in msg:
                        logger.warning(e)
                    else:
                        raise
            cls._initialized = True
            logger.info("Checkpointer initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize checkpointer: {str(e)}")
            cls._initialized = False
            raise

    @classmethod
    async def get_checkpointer(cls) -> AsyncPostgresSaver:
        """
        Get the checkpoint saver instance

        Returns:
            AsyncPostgresSaver: checkpoint saver instance

        Raises:
            RuntimeError: if the checkpoint saver is not initialized
        """
        if cls._checkpointer is None or not cls._initialized:
            raise RuntimeError("Checkpointer not initialized. Call initialize() first.")
        return cls._checkpointer

    @classmethod
    async def close(cls) -> None:
        """Close the checkpoint manager"""
        if cls._checkpointer is not None:
            try:
                # Note: AsyncPostgresSaver doesn't have a close method
                # The connection pool will be managed by DatabaseManager
                cls._checkpointer = None
                cls._initialized = False
                logger.info("Checkpointer closed successfully")
            except Exception as e:
                logger.error(f"Error closing checkpointer: {str(e)}")
                raise
