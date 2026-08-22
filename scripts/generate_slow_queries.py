"""
DBAutonomy — Slow Query Generator

Simulates a slow query scenario for the demo.
Executes a query and measures latency.
"""

from __future__ import annotations

import asyncio
import time
import argparse
import logging
import asyncpg

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

async def run_query(dsn: str, count: int = 5):
    # This query forces a sequence scan on orders table if no index on customer_id exists
    sql = "SELECT * FROM orders WHERE customer_id = 42"
    
    conn = await asyncpg.connect(dsn)
    
    logger.info("=== DBAutonomy Slow Query Demo ===")
    logger.info(f"Target: {dsn.split('@')[-1]}")
    logger.info(f"Executing Query: {sql}")
    logger.info("-" * 40)
    
    # Run explain analyze first
    explain = await conn.fetchval(f"EXPLAIN ANALYZE {sql}")
    logger.info(explain)
    logger.info("-" * 40)
    
    logger.info(f"Running {count} iterations for latency measurement...")
    
    total_ms = 0
    for i in range(count):
        start = time.perf_counter()
        await conn.execute(sql)
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(f"Iteration {i+1}: {elapsed_ms:.1f} ms")
        total_ms += elapsed_ms
        
    avg_ms = total_ms / count
    logger.info("-" * 40)
    logger.info(f"Average Latency: {avg_ms:.1f} ms")
    
    if avg_ms > 10:
        logger.warning(
            "⚠️ Query is performing poorly! "
            "Inject this query into DBAutonomy to automatically deploy an index."
        )
    else:
        logger.info("✅ Query is performing well (index is likely deployed).")
        
    await conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default="postgresql://dbautonomy:dbautonomy@localhost:5432/dbautonomy_primary")
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()
    
    asyncio.run(run_query(args.dsn, args.count))
