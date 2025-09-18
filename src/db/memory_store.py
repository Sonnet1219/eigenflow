"""memory_store.py"""

from typing import Optional
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg.rows import dict_row
import logging
from .database import DatabaseManager

logger = logging.getLogger(__name__)


class MemoryStoreManager:
    """Workflow memory store manager"""

    _instance: Optional["MemoryStoreManager"] = None
    _store: Optional[AsyncPostgresStore] = None
    _db_uri: Optional[str] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    async def initialize(cls, db_uri: str, max_size: int = 20) -> None:
        """
        Initialize the memory store manager

        Args:
            db_uri: database connection URI
            max_size: maximum number of connections in the connection pool
        """
        if cls._store is not None:
            logger.debug("Memory store already initialized, skipping")
            return

        try:
            # Store the database URI for later use
            cls._db_uri = db_uri

            # initialize the connection pool using DatabaseManager
            await DatabaseManager.initialize(db_uri, max_size)
            pool = await DatabaseManager.get_pool()

            # Get a fresh connection for the store
            conn = await pool.getconn()
            conn.row_factory = dict_row

            # initialize the memory store
            cls._store = AsyncPostgresStore(conn)
            await cls._store.setup()
            logger.info("Memory store initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize memory store: {str(e)}")
            raise

    @classmethod
    async def get_store(cls) -> AsyncPostgresStore:
        """
        Get the memory store instance

        Returns:
            AsyncPostgresStore: memory store instance

        Raises:
            RuntimeError: if the memory store is not initialized
        """
        if cls._store is None:
            raise RuntimeError("Memory store not initialized. Call initialize() first.")

        # Check if the store is still valid by testing the connection
        try:
            # Try to access the store to see if it's still working
            await cls._store.aget(("test", "key"), "test_namespace")
        except Exception as e:
            logger.warning(
                f"Memory store connection lost, attempting to reinitialize: {e}"
            )
            # Try to reinitialize with the stored database URI
            if cls._db_uri is not None:
                try:
                    await cls.initialize(cls._db_uri)
                except Exception as init_error:
                    logger.error(f"Failed to reinitialize memory store: {init_error}")
                    raise RuntimeError(
                        "Memory store not initialized. Call initialize() first."
                    )
            else:
                raise RuntimeError(
                    "Memory store not initialized. Call initialize() first."
                )

        return cls._store

    @classmethod
    async def close(cls) -> None:
        """Close the memory store manager"""
        if cls._store is not None:
            try:
                await cls._store.aclose()
            except Exception as e:
                logger.warning(f"Error closing memory store: {e}")
        cls._store = None
        cls._db_uri = None
        logger.info("Memory store closed successfully")