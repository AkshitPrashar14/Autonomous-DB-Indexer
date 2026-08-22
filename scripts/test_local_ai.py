"""
Test Script: Local AI (Qwen / Ollama) Validation
"""

import asyncio
import time
from app.core.config import get_settings
from app.ai.local_parser import LocalLogParser

async def test_qwen_parser():
    settings = get_settings()
    
    # We want to force it to real mode for this test, regardless of env
    settings.AI_MODE = "real"
    
    parser = LocalLogParser(settings)
    
    raw_log = (
        "2023-10-27 10:00:00 UTC [12345]: [1-1] user=postgres,db=testdb LOG:  "
        "duration: 1842.123 ms  statement: SELECT id, name FROM orders WHERE customer_id = 48291 AND status = 'pending';"
    )
    
    print(f"Model: {settings.OLLAMA_MODEL}")
    print(f"Input: {raw_log}")
    
    start_time = time.perf_counter()
    parsed = await parser.parse(raw_log)
    latency = time.perf_counter() - start_time
    
    print(f"Parsed SQL: {parsed.sql}")
    print(f"Tables: {parsed.table_name}")
    print(f"Duration extracted: {parsed.duration_ms}")
    print(f"Query Type: {parsed.query_type}")
    print(f"Latency: {latency:.2f} s")
    print(f"Fallback used: {parsed.parse_source == 'regex_fallback'}")

if __name__ == "__main__":
    asyncio.run(test_qwen_parser())
