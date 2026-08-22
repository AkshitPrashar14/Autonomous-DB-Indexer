"""
DBAutonomy — Demo Replay Script

Injects a controlled sequence of slow queries to deterministically
drive the dashboard demonstration.
"""

import asyncio
import httpx
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

API_URL = "http://localhost:8000/api/jobs/evaluate-and-inject"

def get_random_queries():
    import random
    return [
        (
            "customer aggregate lookup",
            f"SELECT customer_id, SUM(amount) FROM orders WHERE status = '{random.choice(['pending', 'processing', 'shipped', 'delivered', 'cancelled'])}' AND order_date > NOW() - interval '{random.randint(1, 12)} months' GROUP BY customer_id;"
        ),
        (
            "product_id lookup",
            f"SELECT * FROM orders WHERE product_id = {random.randint(1, 1000000)} AND amount > {random.randint(50, 500)};"
        ),
        (
            "category lookup",
            f"SELECT name, price FROM products WHERE category = '{random.choice(['Electronics', 'Books', 'Clothing', 'Home', 'Sports', 'Beauty', 'Toys', 'Automotive', 'Food'])}' AND price BETWEEN {random.randint(10, 50)} AND {random.randint(100, 900)} ORDER BY price DESC;"
        ),
    ]

async def replay_demo():
    logger.info("=== Starting Deterministic Demo Replay ===")
    
    async with httpx.AsyncClient() as client:
        queries = get_random_queries()
        for i, (name, sql) in enumerate(queries, 1):
            logger.info(f"\n[{i}/{len(queries)}] Injecting: {name}")
            logger.info(f"SQL: {sql}")
            
            try:
                response = await client.post(
                    API_URL,
                    json={"sql": sql}
                )
                response.raise_for_status()
                data = response.json()
                logger.info(f"✅ Injected. Job ID: {data['job_id']}")
                
                # Wait 15 seconds before injecting the next one to allow pipeline to run
                if i < len(queries):
                    logger.info("Waiting 15s for pipeline to process...")
                    await asyncio.sleep(15)
                    
            except httpx.HTTPError as e:
                logger.error(f"❌ Failed to inject query: {e}")
                
    logger.info("\n=== Demo Replay Complete ===")

if __name__ == "__main__":
    asyncio.run(replay_demo())
