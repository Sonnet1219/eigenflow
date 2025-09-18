"""
Database connection management module.
Provides centralized database connection handling for async operations.
"""

import sys
import asyncio
from typing import Optional, Dict, Any
from psycopg_pool import AsyncConnectionPool
import psycopg
import psycopg.rows
import logging
import os
import dotenv
import re
from datetime import datetime
from contextlib import asynccontextmanager

# Fix for Windows event loop policy
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Import pgvector automatically registers the vector type with psycopg3

logger = logging.getLogger(__name__)


# Load environment variables
dotenv.load_dotenv()
DB_URI = os.getenv("DATABASE_URL")


# Default database configuration (fallback)
DEFAULT_DB_CONFIG = {
    "dbname": "eulerai",
    "user": "postgres",
    "password": "postgres",
    "host": "localhost",
    "port": "5432",
}


def parse_db_url(url: str) -> dict:
    """
    Parse a PostgreSQL connection URL into component parts

    Args:
        url: Database connection URL in format postgresql://user:pass@host:port/dbname?param=value

    Returns:
        Dictionary with connection parameters
    """
    try:
        # 分离基本URL和查询参数
        if '?' in url:
            base_url, query_string = url.split('?', 1)
        else:
            base_url, query_string = url, ""
        
        regex = (
            r"^postgres(?:ql)?://"
            r"(?P<user>[^:@/]+)"
            r"(?:\:(?P<password>[^@/]*))?@"
            r"(?P<host>[^:/]+)"
            r"(?:\:(?P<port>\d+))?"
            r"/(?P<dbname>[^/?#]+)"
        )
        m = re.match(regex, base_url)
        if not m:
            logger.warning(f"Failed to parse DATABASE_URL: {url!r}")
            return {}
        
        cfg = m.groupdict()
        cfg["password"] = cfg.get("password") or ""
        if cfg.get("port") is None:
            cfg["port"] = "5432"
        
        # 解析查询参数
        if query_string:
            for param in query_string.split('&'):
                if '=' in param:
                    key, value = param.split('=', 1)
                    cfg[key] = value
        
        return {
            "dbname": cfg["dbname"],
            "user": cfg["user"],
            "password": cfg["password"],
            "host": cfg["host"],
            "port": cfg["port"],
            # 添加查询参数到配置中
            **{k: v for k, v in cfg.items() if k not in ["dbname", "user", "password", "host", "port"]}
        }
    except Exception as e:
        logger.error(f"Error parsing DATABASE_URL: {e}")
        return {}


def get_db_config() -> Dict[str, Any]:
    """
    Get database configuration from environment variables or defaults

    Returns:
        Dictionary with database connection parameters
    """
    # First try to get connection parameters from DATABASE_URL
    db_config = {}
    db_url = DB_URI

    if db_url:
        logger.debug("Found DATABASE_URL environment variable")
        db_config = parse_db_url(db_url)

    # If DATABASE_URL parsing failed or wasn't provided, try individual parameters
    if not db_config:
        logger.debug("Using individual database parameters")
        db_config = {
            "dbname": os.getenv("PG_DBNAME", DEFAULT_DB_CONFIG["dbname"]),
            "user": os.getenv("PG_USER", DEFAULT_DB_CONFIG["user"]),
            "password": os.getenv("PG_PASSWORD", DEFAULT_DB_CONFIG["password"]),
            "host": os.getenv("PG_HOST", DEFAULT_DB_CONFIG["host"]),
            "port": os.getenv("PG_PORT", DEFAULT_DB_CONFIG["port"]),
        }

    # Create log-safe config (hide password)
    log_config = db_config.copy()
    if "password" in log_config:
        log_config["password"] = "****"

    logger.debug(f"Database config: {log_config}")
    return db_config


class DatabaseManager:
    """
    Database connection manager using singleton pattern.
    Provides async connection pool for database operations.
    """

    _instance: Optional["DatabaseManager"] = None
    _async_pool: Optional[AsyncConnectionPool] = None
    _last_health_check: Optional[datetime] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    async def initialize(cls, db_uri: str = None, max_size: int = 20) -> None:
        """
        Initialize the async database connection pool

        Args:
            db_uri: Database connection URI (defaults to DB_URI from environment)
            max_size: Maximum number of connections in the pool
        """
        if cls._async_pool is not None:
            # If the connection pool already exists, return directly to avoid repeated initialization
            logger.debug(
                "Async database connection pool already exists, skipping initialization"
            )
            return

        # Use provided URI or environment variable
        uri_to_use = db_uri or DB_URI

        # Add type check to ensure uri_to_use is not None
        if uri_to_use is None:
            raise ValueError(
                "Database URI is not set. Please provide db_uri parameter or set DB_URI environment variable"
            )

        # 解析URL获取配置参数
        url_config = parse_db_url(uri_to_use)
        
        connection_kwargs = {
            "autocommit": True,
            # Avoid psycopg auto-prepared statements to prevent duplicate prepared statement errors
            # during LangGraph checkpointer setup.
            "prepare_threshold": None,
            "connect_timeout": 10,
            "sslmode": url_config.get("sslmode") or os.getenv("SSLMODE", "require"),
            "gssencmode": "disable",
        }

        try:
            cls._async_pool = AsyncConnectionPool(
                conninfo=uri_to_use,
                max_size=max_size,
                open=False,
                kwargs=connection_kwargs,
            )
            await cls._async_pool.open()
            cls._last_health_check = datetime.now()
            logger.info("Async database connection pool initialized successfully")
        except Exception as e:
            logger.error(
                f"Async database connection pool initialization failed: {str(e)}"
            )
            raise

    @classmethod
    async def get_pool(cls, max_retries: int = 3) -> AsyncConnectionPool:
        """
        Get the async database connection pool, with retry mechanism

        Args:
            max_retries: Maximum number of retries

        Returns:
            AsyncConnectionPool: Database connection pool instance

        Raises:
            RuntimeError: If the connection pool is not initialized or the retry fails
        """
        if cls._async_pool is None:
            raise RuntimeError(
                "Async database connection pool not initialized, please call initialize()"
            )

        # Only perform health check if the last health check is more than 5 minutes ago
        now = datetime.now()
        if (
            cls._last_health_check is None
            or (now - cls._last_health_check).total_seconds() > 300
        ):
            if not await cls._check_pool_health():
                for i in range(max_retries):
                    try:
                        await cls._async_pool.close()
                        await cls._async_pool.open()
                        if await cls._check_pool_health():
                            logger.info(
                                "Async database connection pool reconnected successfully"
                            )
                            break
                    except Exception as e:
                        if i == max_retries - 1:
                            logger.error(
                                f"Async database connection pool reconnection failed: {str(e)}"
                            )
                            raise RuntimeError(
                                "Async database connection pool reconnection failed"
                            )
                        await asyncio.sleep(1)

        return cls._async_pool

    @classmethod
    async def _check_pool_health(cls) -> bool:
        """
        Check the health status of the async connection pool

        Returns:
            bool: True if the pool is healthy, False otherwise
        """
        if cls._async_pool is None:
            return False

        try:
            async with cls._async_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
            cls._last_health_check = datetime.now()
            return True
        except Exception as e:
            logger.error(
                f"Async database connection pool health check failed: {str(e)}"
            )
            return False

    @classmethod
    @asynccontextmanager
    async def get_connection(cls):
        """
        Get an async connection from the pool with context management

        Usage:
            async with DatabaseManager.get_connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")

        Returns:
            Async database connection from the pool

        Raises:
            RuntimeError: If the pool is not initialized
        """
        if cls._async_pool is None:
            await cls.initialize()
            if cls._async_pool is None:
                raise RuntimeError(
                    "Async database connection pool initialization failed"
                )

        async with cls._async_pool.connection() as conn:
            yield conn

    @classmethod
    @asynccontextmanager
    async def get_dict_cursor(cls, conn):
        """
        Get a cursor that returns dictionaries from rows

        Args:
            conn: Async database connection

        Yields:
            Async cursor with dict row factory
        """
        # In psycopg3, row factories are specified when creating the cursor
        async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            yield cur

    @classmethod
    async def close(cls) -> None:
        """
        Close the async database connection pool
        """
        if cls._async_pool is not None:
            try:
                await cls._async_pool.close()
                cls._async_pool = None
                logger.info("Async database connection pool closed successfully")
            except Exception as e:
                logger.error(
                    f"Failed to close async database connection pool: {str(e)}"
                )
                raise
