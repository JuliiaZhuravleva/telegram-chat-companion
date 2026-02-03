"""
Database connection management with asyncpg and pgvector support.
"""

import asyncpg
from pgvector.asyncpg import register_vector


async def create_pool(dsn: str, min_size: int = 5, max_size: int = 20) -> asyncpg.Pool:
    """
    Create a connection pool with pgvector support.

    Args:
        dsn: PostgreSQL connection string
        min_size: Minimum number of connections in the pool
        max_size: Maximum number of connections in the pool

    Returns:
        Configured connection pool
    """

    async def init_connection(conn: asyncpg.Connection) -> None:
        """Initialize each connection with pgvector."""
        await register_vector(conn)

    pool = await asyncpg.create_pool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        init=init_connection,
    )

    if pool is None:
        raise RuntimeError("Failed to create database connection pool")

    return pool


async def close_pool(pool: asyncpg.Pool) -> None:
    """Close the connection pool gracefully."""
    await pool.close()
