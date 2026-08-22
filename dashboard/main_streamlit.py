import os
import sys
import time
import json
import random
import asyncio
import streamlit as st
import re

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import get_settings
from app.models.domain import ParsedQuery, QueryType, BenchmarkResult, IndexCandidate, IndexType, OptimizationStatus
from app.ai.candidate_gen import CandidateGenerator
from app.ai.context_builder import ContextBuilder
from app.evaluation.safety_gate import SafetyGate
from app.evaluation.reward_calculator import RewardCalculator
from app.agent.features import FeatureExtractor
from app.agent.bandit import BanditPolicy
from app.database.schema import SchemaInspector, TableSchema, ColumnSchema

# Optional DB
try:
    from app.database.shadow_manager import ShadowDatabaseManager
    from app.database.benchmark import BenchmarkRunner
except ImportError:
    ShadowDatabaseManager = None
    BenchmarkRunner = None

st.set_page_config(
    page_title="DBAutonomy — Hosted Demo",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
  .step-box { padding: 10px; margin-bottom: 5px; border-radius: 5px; border-left: 4px solid #4f8ef7; background: #1e2a3a; }
  .active-step { border-color: #f59e0b; animation: pulse 1s infinite; background: #2d3748; }
  .completed-step { border-color: #22c55e; }
  .rejected-step { border-color: #ef4444; }
  .event-log { font-family: monospace; font-size: 0.85em; background: #0e1117; padding: 10px; height: 300px; overflow-y: scroll; border-radius: 5px; }
  @keyframes pulse { 0%{opacity:1} 50%{opacity:.7} 100%{opacity:1} }
</style>
""", unsafe_allow_html=True)

# Initialize Session State
if "events" not in st.session_state:
    st.session_state.events = []
if "metadata" not in st.session_state:
    st.session_state.metadata = {}
if "states_seen" not in st.session_state:
    st.session_state.states_seen = set()
if "active_state" not in st.session_state:
    st.session_state.active_state = None
if "bandit_state" not in st.session_state:
    # Initialize a fresh bandit if not present
    settings = get_settings()
    st.session_state.bandit = BanditPolicy(settings)
    st.session_state.bandit_stats = {"updates": 0}

def emit_event(state, message, meta_updates=None):
    ts = time.strftime("%H:%M:%S")
    st.session_state.events.append(f"{ts}  {message}")
    st.session_state.active_state = state
    st.session_state.states_seen.add(state)
    if meta_updates:
        st.session_state.metadata.update(meta_updates)

async def run_pipeline(sql: str):
    st.session_state.events = []
    st.session_state.metadata = {}
    st.session_state.states_seen = set()
    st.session_state.active_state = None
    
    settings = get_settings()
    # Inject API Keys from Streamlit Secrets if available
    if hasattr(st, "secrets"):
        if "GEMINI_API_KEY" in st.secrets:
            settings.GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
        if "GROQ_API_KEY" in st.secrets:
            settings.GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
    
    # 1. Detect
    emit_event("DETECTED", "Slow query detected", {"raw_log_preview": sql})
    yield
    
    # 2. Parse (Deterministic Fallback)
    await asyncio.sleep(0.5)
    # Very naive regex for demo purposes
    table_match = re.search(r"FROM\s+(\w+)", sql, re.IGNORECASE)
    table_name = table_match.group(1).lower() if table_match else "unknown"
    
    parsed = ParsedQuery(
        sql=sql,
        duration_ms=random.randint(500, 2500),
        table_name=table_name,
        query_type=QueryType.SELECT,
        where_columns=[],
        join_tables=[],
        order_by_columns=[],
        parse_source="deterministic_regex",
        confidence=1.0,
    )
    emit_event("PARSED", "Query parsed (Deterministic Fallback)", {
        "table_name": parsed.table_name,
        "duration_ms": parsed.duration_ms,
        "sql": parsed.sql
    })
    yield
    
    # 3. Schema
    await asyncio.sleep(0.5)
    # Mock schema for hosted mode since we can't reliably connect to a DB
    schema = TableSchema(
        table_name=table_name,
        row_count=5000000,
        columns=[
            ColumnSchema(name="id", data_type="integer"),
            ColumnSchema(name="customer_id", data_type="integer"),
            ColumnSchema(name="product_id", data_type="integer"),
            ColumnSchema(name="status", data_type="text"),
            ColumnSchema(name="amount", data_type="numeric"),
        ],
        existing_indexes=[]
    )
    emit_event("SCHEMA_ANALYZED", f"Schema retrieved for {table_name} (Mocked for Hosted)", {
        "schema": schema.model_dump()
    })
    yield
    
    # 4. Candidates
    context_builder = ContextBuilder()
    context = context_builder.build(parsed, schema, [])
    
    gen = CandidateGenerator(settings)
    try:
        candidates = await gen.generate(context)
        emit_event("CANDIDATES_GENERATED", f"Generated {len(candidates)} candidates", {
            "candidates": [
                {
                    "fingerprint": c.fingerprint,
                    "columns": c.columns,
                    "type": c.index_type.value,
                    "explanation": c.explanation
                } for c in candidates
            ]
        })
    except Exception as e:
        emit_event("FAILED", f"Failed to generate candidates: {e}")
        yield
        return
    yield
    
    # 5. Bandit
    await asyncio.sleep(0.5)
    bandit = st.session_state.bandit
    extractor = FeatureExtractor(settings)
    
    valid_candidates = []
    safety_gate = SafetyGate(settings)
    for c in candidates:
        if safety_gate.structural_check(c):
            valid_candidates.append(c)
            
    if not valid_candidates:
        emit_event("REJECTED", "No valid candidates passed structural safety")
        yield
        return
        
    shared_context = extractor.extract(context, valid_candidates[0])
    chosen = bandit.select(shared_context, valid_candidates)
    
    emit_event("BANDIT_SELECTED", f"LinUCB selected {chosen.columns}", {
        "chosen_index": valid_candidates.index(chosen),
        "scores": [{"index": i, "ucb": bandit._calculate_ucb(extractor.extract(context, c).to_numpy(), c.index_type.value)} for i, c in enumerate(valid_candidates)]
    })
    yield
    
    # 6. Shadow Benchmark
    await asyncio.sleep(1)
    emit_event("SHADOW_STARTED", "Initiating Shadow Test")
    yield
    
    # In hosted mode, we cannot safely assume a Postgres DB is available for benchmarking.
    # The requirement explicitly says DO NOT FAKE BENCHMARK RESULTS.
    emit_event("REJECTED", "Real PostgreSQL benchmark unavailable in hosted mode.", {
        "reason": "Hosted Streamlit Cloud environment lacks a local PostgreSQL shadow database to safely execute EXPLAIN ANALYZE."
    })
    yield

def trigger_pipeline(sql):
    async def run():
        async for _ in run_pipeline(sql):
            pass # We don't yield anymore in run_pipeline, wait, we do.
    # Actually, we can just run it sequentially and use a placeholder
    pass

# We will move all rendering to a function
def render_ui():
    # HEADER
    st.title("AUTONOMOUS DATABASE INDEXER")
    st.markdown("┌─────────────────────────────────────────┐")
    st.markdown("│ **🟢 HOSTED STREAMLIT DEMO** │")
    st.markdown("└─────────────────────────────────────────┘")
    st.markdown("Local full-AI mode: **Qwen + Gemini** | Hosted mode: **Gemini / deterministic parser fallback**")
    st.divider()

    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.subheader("MAIN PIPELINE")
        
        steps = [
            ("DETECTED", "1. 🔍 Slow Query Detected"),
            ("PARSED", "2. 🧠 AI Parsed"),
            ("SCHEMA_ANALYZED", "3. 📐 Schema Analyzed"),
            ("CANDIDATES_GENERATED", "4. 🤖 Gemini Candidates"),
            ("BANDIT_SELECTED", "5. 🎯 LinUCB Selected"),
            ("SHADOW_STARTED", "6. 🧪 Shadow Tested"),
            ("REWARD_CALCULATED", "7. 📊 Reward Calculated"),
            ("SAFETY_EVALUATED", "8. 🛡 Safety Gate"),
            ("DEPLOYED", "9. 🚀 Deploy / Reject"),
        ]
        
        for s_code, s_label in steps:
            css_class = "step-box"
            if st.session_state.active_state == s_code:
                css_class += " active-step"
            elif s_code in st.session_state.states_seen or (s_code == "SHADOW_STARTED" and ("BASELINE_COMPLETE" in st.session_state.states_seen or "CANDIDATE_COMPLETE" in st.session_state.states_seen)):
                css_class += " completed-step"
                
            if s_code == "DEPLOYED" and "REJECTED" in st.session_state.states_seen:
                css_class = "step-box rejected-step"
                s_label = "9. 🚫 REJECTED"
                
            st.markdown(f"<div class='{css_class}'>{s_label}</div>", unsafe_allow_html=True)

        st.subheader("LIVE EVENT STREAM")
        event_lines = st.session_state.events[-20:]
        st.markdown(f"<div class='event-log'>{'<br>'.join(event_lines)}</div>", unsafe_allow_html=True)


    with col_right:
        st.subheader("QUERY PANEL")
        meta = st.session_state.metadata
        if meta.get("raw_log_preview"):
            st.code(meta.get("sql", meta.get("raw_log_preview")), language="sql")
            st.write(f"**Duration:** {meta.get('duration_ms', '?')} ms | **Table:** {meta.get('table_name', '?')}")
            
            if meta.get("schema"):
                st.markdown(f"**Schema for `{meta.get('table_name')}`:**")
                cols = meta.get("schema", {}).get("columns", [])
                col_defs = ", ".join([f"{c['name']} ({c['data_type']})" for c in cols])
                st.info(col_defs)
        else:
            st.info("Waiting for query...")

        st.subheader("AI PANEL")
        st.markdown("**PARSER** - *Deterministic Fallback*")
        st.write(f"Output: Table `{meta.get('table_name', '')}`")
        
        st.markdown("**CANDIDATE GENERATION** - *Gemini*")
        candidates = meta.get("candidates", [])
        for i, c in enumerate(candidates):
            st.code(f"-- Candidate {i+1}\nCREATE INDEX ON {meta.get('table_name')} ({', '.join(c['columns'])});", language="sql")

        st.subheader("BANDIT PANEL")
        scores = meta.get("scores", [])
        if scores:
            for s in scores:
                selected = "← SELECTED" if s['index'] == meta.get("chosen_index") else ""
                st.write(f"Candidate {s['index']+1} | UCB: {s['ucb']:.2f} {selected}")

        st.subheader("EXPERIMENT PANEL")
        if "SHADOW_STARTED" in st.session_state.states_seen:
            if "REJECTED" in st.session_state.states_seen and "unavailable" in meta.get("reason", ""):
                st.error(meta.get("reason"))
            else:
                col1, col2, col3 = st.columns(3)
                col1.metric("Baseline", f"{meta.get('baseline_p50', 0):.1f} ms")
                col2.metric("Candidate", f"{meta.get('experiment_p50', 0):.1f} ms")
                col3.metric("Improvement", f"{meta.get('improvement_pct', 0):.1f}%")

        st.subheader("DEPLOYMENT PANEL")
        if "DEPLOYED" in st.session_state.states_seen:
            st.success(f"DEPLOYED: {meta.get('index_name')}")
        elif "REJECTED" in st.session_state.states_seen:
            st.error(f"REJECTED: {meta.get('reason', '')}")

    st.divider()

    st.subheader("DEMO CONTROLS")
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("RUN: Successful Optimization"):
            trigger_pipeline_sync("SELECT customer_id, SUM(amount) FROM orders WHERE status = 'pending' AND order_date > NOW() - interval '1 months' GROUP BY customer_id;")
    with c2:
        if st.button("RUN: Candidate Comparison"):
            trigger_pipeline_sync("SELECT * FROM products WHERE category = 'Electronics' AND price BETWEEN 10 AND 50 ORDER BY price DESC;")
    with c3:
        if st.button("RUN: Rejected Optimization"):
            trigger_pipeline_sync("SELECT category, COUNT(*) FROM events WHERE event_type = 'click' GROUP BY category;")

ui_placeholder = st.empty()

def trigger_pipeline_sync(sql):
    # This runs the async generator sequentially and updates the UI placeholder
    async def run():
        async for _ in run_pipeline(sql):
            with ui_placeholder.container():
                render_ui()
    asyncio.run(run())

# Initial render
with ui_placeholder.container():
    render_ui()
