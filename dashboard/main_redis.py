"""
DBAutonomy — Streamlit Live Dashboard
"""

import json
import os
import time
import requests
import streamlit as st
import redis

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
POLL_INTERVAL_S = 1

st.set_page_config(
    page_title="DBAutonomy — Demo Dashboard",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Initialize Redis connection
@st.cache_resource
def get_redis():
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)

redis_client = get_redis()

st.markdown("""
<style>
  .step-box { padding: 10px; margin-bottom: 5px; border-radius: 5px; border-left: 4px solid #4f8ef7; background: #1e2a3a; }
  .active-step { border-color: #f59e0b; animation: pulse 1s infinite; background: #2d3748; }
  .completed-step { border-color: #22c55e; }
  .rejected-step { border-color: #ef4444; }
  .event-log { font-family: monospace; font-size: 0.85em; background: #0e1117; padding: 10px; height: 300px; overflow-y: scroll; border-radius: 5px; }
  @keyframes pulse { 0%{opacity:1} 50%{opacity:.7} 100%{opacity:1} }
  .metric-card { background: #1e2a3a; padding: 15px; border-radius: 8px; text-align: center; }
</style>
""", unsafe_allow_html=True)

def fetch_events():
    history = redis_client.lrange("dbautonomy:events:history", 0, -1)
    events = []
    for item in history:
        try:
            ev = json.loads(item)
            events.append(ev)
        except Exception:
            pass
    return events

events = fetch_events()
current_job_id = None
job_events = []
if events:
    # Group by job_id, select the most recent job
    latest_job = events[-1]["payload"].get("job_id")
    current_job_id = latest_job
    job_events = [e for e in events if e["payload"].get("job_id") == current_job_id]

# Determine active state
active_state = job_events[-1]["payload"].get("state") if job_events else None
metadata = {}
for e in job_events:
    metadata.update(e["payload"].get("metadata", {}))

# HEADER
st.title("AUTONOMOUS DATABASE INDEXER")
demo_mode_str = os.getenv("DEMO_MODE", "false").lower()
is_demo = demo_mode_str in ("true", "1", "yes")
mode_text = "DEMO MODE" if is_demo else "REAL AI"
st.markdown(f"Status: **● ONLINE** | Mode: **{mode_text}**")
c1, c2, c3, c4, c5 = st.columns(5)
c1.markdown("Qwen ●")
c2.markdown("Gemini ●")
c3.markdown("LinUCB ●")
c4.markdown("Postgres ●")
c5.markdown("Redis ●")
st.divider()

col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader("MAIN PIPELINE")
    
    steps = [
        ("DETECTED", "1. 🔍 Slow Query Detected"),
        ("PARSED", "2. 🧠 Qwen Parsed"),
        ("SCHEMA_ANALYZED", "3. 📐 Schema Analyzed"),
        ("CANDIDATES_GENERATED", "4. 🤖 Gemini Generated Candidates"),
        ("BANDIT_SELECTED", "5. 🎯 LinUCB Selected"),
        ("SHADOW_STARTED", "6. 🧪 Shadow Tested"),
        ("REWARD_CALCULATED", "7. 📊 Reward Calculated"),
        ("SAFETY_EVALUATED", "8. 🛡 Safety Gate"),
        ("DEPLOYED", "9. 🚀 Deploy / Reject"),
    ]
    
    states_seen = {e["payload"].get("state") for e in job_events}
    
    for s_code, s_label in steps:
        css_class = "step-box"
        if active_state == s_code:
            css_class += " active-step"
        elif s_code in states_seen or (s_code == "SHADOW_STARTED" and ("BASELINE_COMPLETE" in states_seen or "CANDIDATE_COMPLETE" in states_seen)):
            css_class += " completed-step"
            
        if s_code == "DEPLOYED" and "REJECTED" in states_seen:
            css_class = "step-box rejected-step"
            s_label = "9. 🚫 REJECTED"
            
        st.markdown(f"<div class='{css_class}'>{s_label}</div>", unsafe_allow_html=True)

    st.subheader("LIVE EVENT STREAM")
    event_lines = []
    for e in events[-20:]:  # show last 20 globally
        ts = e.get("timestamp", "")[11:19]
        msg = e.get("payload", {}).get("message", e.get("event_type", ""))
        event_lines.append(f"{ts}  {msg}")
    st.markdown(f"<div class='event-log'>{'<br>'.join(event_lines)}</div>", unsafe_allow_html=True)


with col_right:
    st.subheader("QUERY PANEL")
    if metadata.get("raw_log_preview"):
        st.code(metadata.get("sql", metadata.get("raw_log_preview")), language="sql")
        st.write(f"**Duration:** {metadata.get('duration_ms', '?')} ms | **Table:** {metadata.get('table_name', '?')}")
        
        # Display schema if available
        if metadata.get("schema"):
            st.markdown(f"**Schema for `{metadata.get('table_name')}` (Rows: {metadata.get('schema', {}).get('row_count', 'N/A')}):**")
            cols = metadata.get("schema", {}).get("columns", [])
            if cols:
                col_defs = ", ".join([f"{c['name']} ({c['data_type']})" for c in cols])
                st.info(col_defs)
    else:
        st.info("Waiting for query...")

    st.subheader("AI PANEL")
    st.markdown("**LOCAL MODEL (Qwen2.5-Coder 3B)** - *Task: Log Parsing*")
    st.write(f"Output: Parsed SQL + table `{metadata.get('table_name', '')}`")
    
    st.markdown("**CLOUD MODEL (Gemini)** - *Task: Index Candidate Generation*")
    candidates = metadata.get("candidates", [])
    for i, c in enumerate(candidates):
        st.code(f"-- Candidate {i+1}\nCREATE INDEX ON {metadata.get('table_name')} ({', '.join(c['columns'])});", language="sql")

    st.subheader("BANDIT PANEL")
    st.markdown("Contextual Bandit: **LinUCB** | Alpha: **1.0**")
    scores = metadata.get("scores", [])
    if scores:
        for s in scores:
            selected = "← SELECTED" if s['index'] == metadata.get("chosen_index") else ""
            st.write(f"Candidate {s['index']+1} | UCB: {s['ucb']:.2f} {selected}")

    st.subheader("EXPERIMENT PANEL")
    st.write("**SHADOW DATABASE**")
    col1, col2, col3 = st.columns(3)
    col1.metric("Baseline", f"{metadata.get('baseline_p50', 0):.1f} ms")
    col2.metric("Candidate", f"{metadata.get('experiment_p50', 0):.1f} ms")
    col3.metric("Improvement", f"{metadata.get('improvement_pct', 0):.1f}%")

    st.subheader("REWARD PANEL")
    r = metadata.get("reward")
    if r is not None:
        st.write(f"**Final reward:** {r:.3f}")

    st.subheader("SAFETY PANEL")
    if "SAFETY_EVALUATED" in states_seen:
        approved = metadata.get("approved", False)
        status_text = "APPROVED" if approved else "REJECTED"
        st.markdown(f"**SAFETY GATE: {status_text}**")
        if not approved:
            st.error(f"Reason: {metadata.get('reason')}")

    st.subheader("DEPLOYMENT PANEL")
    if "DEPLOYED" in states_seen:
        st.success(f"DEPLOYED: {metadata.get('index_name')}")
    elif "REJECTED" in states_seen:
        st.error("REJECTED")

st.divider()

st.subheader("BANDIT LEARNING VISUALIZATION")
# Fetch stats from API
try:
    bandit_stats = requests.get(f"{API_BASE}/api/bandit", timeout=2).json()
    st.write(f"Observation count: **{bandit_stats.get('total_updates', 0)}**")
except:
    st.write("Observation count: N/A")

st.divider()

st.subheader("DEMO CONTROLS")
c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    if st.button("INJECT RANDOM QUERY"):
        import random
        queries = [
            f"SELECT customer_id, SUM(amount) FROM orders WHERE status = '{random.choice(['pending', 'processing', 'shipped', 'delivered', 'cancelled'])}' AND order_date > NOW() - interval '{random.randint(1, 12)} months' GROUP BY customer_id;",
            f"SELECT * FROM orders WHERE product_id = {random.randint(1, 1000000)} AND amount > {random.randint(50, 500)};",
            f"SELECT * FROM orders WHERE customer_id = {random.randint(1, 100000)} ORDER BY order_date DESC LIMIT {random.choice([10, 50, 100])};",
            f"SELECT name, price FROM products WHERE category = '{random.choice(['Electronics', 'Books', 'Clothing', 'Home', 'Sports', 'Beauty', 'Toys', 'Automotive', 'Food'])}' AND price BETWEEN {random.randint(10, 50)} AND {random.randint(100, 900)} ORDER BY price DESC;",
            f"SELECT category, COUNT(*) FROM products WHERE price > {random.randint(100, 500)} GROUP BY category;",
            f"SELECT o.customer_id, p.name FROM orders o JOIN products p ON o.product_id = p.id WHERE o.status = '{random.choice(['pending', 'processing'])}' LIMIT 100;"
        ]
        sql = random.choice(queries)
        requests.post(f"{API_BASE}/api/jobs/evaluate-and-inject", json={"sql": sql})
with c2:
    if st.button("ASK GEMINI FOR QUERY"):
        import os
        import google.generativeai as genai
        import re
        
        api_key = os.environ.get("GEMINI_API_KEY", "your_gemini_api_key_here")
        genai.configure(api_key=api_key)
        
        prompt = """
        You are an expert PostgreSQL DBA. Generate ONE highly complex, slow, and realistic SELECT query 
        for a demo database that needs index optimization. 
        
        The database has four tables:
        1. orders (id, customer_id, product_id, status, amount, order_date)
        2. products (id, name, category, price, stock, created_at)
        3. order_items (id, order_id, product_id, quantity, price)
        4. events (id, user_id, event_type, payload, created_at)
        
        Use random large numbers, complex WHERE clauses, JOINs, or aggregations (GROUP BY).
        Return ONLY the raw SQL query string. No markdown formatting, no explanations. Just the SQL.
        """
        try:
            model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            sql = response.text.strip().strip("`").strip()
            if sql.lower().startswith("sql"):
                sql = sql[3:].strip()
            requests.post(f"{API_BASE}/api/jobs/evaluate-and-inject", json={"sql": sql})
            st.success("Evaluated and Injected Gemini Query!")
        except Exception as e:
            st.error(f"Failed: {e}")

with c3:
    if st.button("ASK GROQ FOR QUERY"):
        import os
        from openai import OpenAI
        
        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            st.error("GROQ_API_KEY not found in .env")
        else:
            try:
                client = OpenAI(
                    api_key=api_key,
                    base_url="https://api.groq.com/openai/v1",
                )
                prompt = """
                You are an expert PostgreSQL DBA. Generate ONE highly complex, slow, and realistic SELECT query 
                for a demo database that needs index optimization. 
                
                The database has four tables:
                1. orders (id, customer_id, product_id, status, amount, order_date)
                2. products (id, name, category, price, stock, created_at)
                3. order_items (id, order_id, product_id, quantity, price)
                4. events (id, user_id, event_type, payload, created_at)
                
                Use random large numbers, complex WHERE clauses, JOINs, or aggregations (GROUP BY).
                Return ONLY the raw SQL query string. No markdown formatting, no explanations. Just the SQL.
                """
                response = client.chat.completions.create(
                    model="openai/gpt-oss-120b",
                    messages=[
                        {"role": "system", "content": prompt}
                    ],
                    temperature=0.2,
                )
                sql = response.choices[0].message.content.strip().strip("`").strip()
                if sql.lower().startswith("sql"):
                    sql = sql[3:].strip()
                requests.post(f"{API_BASE}/api/jobs/evaluate-and-inject", json={"sql": sql})
                st.success("Evaluated and Injected Groq Query!")
            except Exception as e:
                st.error(f"Failed to generate query with Groq: {e}")

with c4:
    if st.button("REFRESH"):
        pass
with c5:
    if st.button("CLEAR DEMO STATE"):
        redis_client.delete("dbautonomy:events:history")
        requests.post(f"{API_BASE}/api/debug/clear") # Assuming this exists or just fails gracefully

time.sleep(POLL_INTERVAL_S)
st.rerun()
