import os
import json
import asyncio
import asyncpg

async def main():
    dsn = os.environ.get(
        "DATABASE_URL", 
        "postgresql://dbautonomy:dbautonomy@localhost:5440/dbautonomy_primary"
    )
    scale = os.environ.get("DEMO_SCALE", "large").lower()
    
    if scale == "small":
        num_products = int(os.environ.get("DEMO_PRODUCTS", 100000))
        num_orders = int(os.environ.get("DEMO_ORDERS", 500000))
        num_order_items = int(os.environ.get("DEMO_ORDER_ITEMS", 1000000))
        num_events = int(os.environ.get("DEMO_EVENTS", 300000))
    else:
        num_products = int(os.environ.get("DEMO_PRODUCTS", 500000))
        num_orders = int(os.environ.get("DEMO_ORDERS", 1000000))
        num_order_items = int(os.environ.get("DEMO_ORDER_ITEMS", 2500000))
        num_events = int(os.environ.get("DEMO_EVENTS", 1000000))

    print(f"Connecting to database... (Scale: {scale})")
    conn = await asyncpg.connect(dsn)

    # 1. Drop and create tables
    await conn.execute("""
        DROP TABLE IF EXISTS events CASCADE;
        DROP TABLE IF EXISTS order_items CASCADE;
        DROP TABLE IF EXISTS orders CASCADE;
        DROP TABLE IF EXISTS products CASCADE;

        CREATE TABLE products (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price NUMERIC(10,2) NOT NULL,
            stock INT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE orders (
            id BIGSERIAL PRIMARY KEY,
            customer_id BIGINT NOT NULL,
            product_id BIGINT REFERENCES products(id),
            status TEXT NOT NULL,
            amount NUMERIC(10,2) NOT NULL,
            order_date TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE order_items (
            id BIGSERIAL PRIMARY KEY,
            order_id BIGINT NOT NULL REFERENCES orders(id),
            product_id BIGINT NOT NULL REFERENCES products(id),
            quantity INT NOT NULL,
            price NUMERIC(10,2) NOT NULL
        );

        CREATE TABLE events (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            event_type TEXT NOT NULL,
            payload JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    # 2. Generate Data using generate_series
    print("Generating products...")
    await conn.execute(f"""
        INSERT INTO products (name, category, price, stock)
        SELECT
            'Product ' || g,
            CASE g % 10
                WHEN 0 THEN 'Electronics'
                WHEN 1 THEN 'Books'
                WHEN 2 THEN 'Clothing'
                WHEN 3 THEN 'Home'
                WHEN 4 THEN 'Sports'
                WHEN 5 THEN 'Beauty'
                WHEN 6 THEN 'Toys'
                WHEN 7 THEN 'Automotive'
                WHEN 8 THEN 'Food'
                ELSE 'Other'
            END,
            round((10 + random() * 990)::numeric, 2),
            floor(random() * 1000)::int
        FROM generate_series(1, {num_products}) g;
    """)

    print("Generating orders...")
    # customer_id skew: random()^3 gives a curve heavily biased to 0, * 100k
    await conn.execute(f"""
        INSERT INTO orders (customer_id, product_id, status, amount, order_date)
        SELECT
            1 + floor((random() ^ 3) * 100000)::bigint,
            1 + floor(random() * {num_products - 1})::bigint,
            CASE floor(random() * 5)
                WHEN 0 THEN 'pending'
                WHEN 1 THEN 'processing'
                WHEN 2 THEN 'shipped'
                WHEN 3 THEN 'delivered'
                ELSE 'cancelled'
            END,
            round((10 + random() * 1990)::numeric, 2),
            NOW() - (random() * interval '3 years')
        FROM generate_series(1, {num_orders});
    """)

    print("Generating order_items...")
    await conn.execute(f"""
        INSERT INTO order_items (order_id, product_id, quantity, price)
        SELECT
            1 + floor(random() * {num_orders - 1})::bigint,
            1 + floor(random() * {num_products - 1})::bigint,
            1 + floor(random() * 5)::int,
            round((10 + random() * 100)::numeric, 2)
        FROM generate_series(1, {num_order_items});
    """)

    print("Generating events...")
    # user_id skew similar to customer_id
    await conn.execute(f"""
        INSERT INTO events (user_id, event_type, payload, created_at)
        SELECT
            1 + floor((random() ^ 3) * 100000)::bigint,
            CASE floor(random() * 4)
                WHEN 0 THEN 'login'
                WHEN 1 THEN 'view_product'
                WHEN 2 THEN 'add_to_cart'
                ELSE 'checkout'
            END,
            '{{"browser": "chrome"}}'::jsonb,
            NOW() - (random() * interval '3 years')
        FROM generate_series(1, {num_events});
    """)

    # 3. Analyze tables
    print("Executing ANALYZE...")
    await conn.execute("ANALYZE products, orders, order_items, events;")

    # 4. Print stats
    print("\n--- DATABASE STATISTICS ---")
    print(f"{'Table':<15} | {'Rows':<12} | {'Size':<10} | {'Indexes'}")
    print("-" * 75)
    
    tables = ["products", "orders", "order_items", "events"]
    for t in tables:
        stats = await conn.fetchrow(f"""
            SELECT 
                reltuples::bigint as rows,
                pg_size_pretty(pg_total_relation_size('{t}')) as size
            FROM pg_class 
            WHERE relname = '{t}';
        """)
        indexes = await conn.fetch(f"""
            SELECT indexname 
            FROM pg_indexes 
            WHERE tablename = '{t}';
        """)
        idx_str = ", ".join([idx['indexname'] for idx in indexes])
        print(f"{t:<15} | {stats['rows']:<12} | {stats['size']:<10} | {idx_str}")


    # 5. Benchmark queries
    queries = [
        """
        SELECT *
        FROM orders
        WHERE customer_id = 48291;
        """,
        """
        SELECT *
        FROM orders
        WHERE customer_id = 48291
        ORDER BY order_date DESC
        LIMIT 20;
        """,
        """
        SELECT *
        FROM events
        WHERE user_id = 73192
        ORDER BY created_at DESC
        LIMIT 50;
        """
    ]

    print("\n--- BENCHMARK QUERIES ---")
    for i, q in enumerate(queries, 1):
        print(f"\nQuery {i}:\n{q.strip()}")
        # Run EXPLAIN ANALYZE BUFFERS FORMAT JSON
        explain_sql = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {q}"
        try:
            res = await conn.fetchval(explain_sql)
            plan = json.loads(res)[0]["Plan"]
            
            execution_time = json.loads(res)[0].get("Execution Time", 0.0)
            planning_time = json.loads(res)[0].get("Planning Time", 0.0)
            
            # Simple recursive search for scan type, rows, buffers in the plan tree
            scan_type = plan.get("Node Type", "Unknown")
            rows = plan.get("Actual Rows", 0)
            
            # Extract buffers from the top node if available, or recursively
            def get_buffers(node):
                b = node.get("Shared Hit Blocks", 0) + node.get("Shared Read Blocks", 0)
                for plan in node.get("Plans", []):
                    b += get_buffers(plan)
                return b
            
            buffers = get_buffers(plan)

            print(f"  Execution Time: {execution_time:.3f} ms")
            print(f"  Planning Time:  {planning_time:.3f} ms")
            print(f"  Scan Type:      {scan_type}")
            print(f"  Rows Returned:  {rows}")
            print(f"  Buffers Hit:    {buffers}")
        except Exception as e:
            print(f"Error benchmarking query {i}: {e}")

    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
