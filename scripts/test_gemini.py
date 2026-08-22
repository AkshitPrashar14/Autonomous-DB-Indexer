"""
Test Script: Gemini API Validation
"""

import asyncio
import time
from app.core.config import get_settings
from app.ai.candidate_gen import CandidateGenerator
from app.models.domain import OptimizationContext, ParsedQuery, QueryType

async def test_gemini_generator():
    settings = get_settings()
    
    # We want to force it to real mode for this test
    settings.AI_MODE = "real"
    
    generator = CandidateGenerator(settings)
    
    parsed = ParsedQuery(
        sql="SELECT id, name FROM orders WHERE customer_id = 48291 AND status = 'pending';",
        duration_ms=1842.0,
        table_name="orders",
        query_type=QueryType.SELECT,
        where_columns=["customer_id", "status"],
        join_tables=[],
        order_by_columns=[],
        parse_source="test",
        confidence=1.0,
    )
    
    from app.models.domain import TableSchema, ColumnInfo
    
    schema = TableSchema(
        table_name="orders",
        row_count=100000,
        columns=[
            ColumnInfo(name="id", data_type="integer", is_nullable=False, is_primary_key=True),
            ColumnInfo(name="customer_id", data_type="integer", is_nullable=False, is_primary_key=False),
            ColumnInfo(name="status", data_type="text", is_nullable=True, is_primary_key=False),
            ColumnInfo(name="name", data_type="text", is_nullable=True, is_primary_key=False),
        ],
        existing_indexes=[]
    )
    
    context = OptimizationContext(
        parsed_query=parsed,
        schema=schema,
        existing_indexes=[],
        index_sizes={}
    )
    
    print("Testing Gemini Candidate Generation...")
    
    start_time = time.perf_counter()
    candidates = await generator.generate(context)
    latency = time.perf_counter() - start_time
    
    print(f"Candidates generated: {len(candidates)}")
    for i, candidate in enumerate(candidates):
        print(f"\nCandidate {i+1}:")
        print(f"SQL: CREATE INDEX dbautonomy_idx ON {candidate.table_name} ({', '.join(candidate.columns)});")
        print(f"Rationale: {candidate.explanation}")
        print(f"Indexed columns: {candidate.columns}")
        
    print(f"\nLatency: {latency:.2f} s")
    print(f"Validation result: {len(candidates)} valid candidates.")

if __name__ == "__main__":
    asyncio.run(test_gemini_generator())
