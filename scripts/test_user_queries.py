import requests
import time

API_URL = "http://localhost:8000/api/jobs/inject"

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
    ORDER BY created_at DESC
    LIMIT 20;
    """
]

for i, q in enumerate(queries):
    print(f"Injecting Query {i+1}...")
    res = requests.post(API_URL, json={"raw_log": f"duration: 2314.5 ms statement: {q.strip()}"})
    print(res.json())
    if i < len(queries) - 1:
        print("Waiting 15 seconds for pipeline to process...")
        time.sleep(15)
