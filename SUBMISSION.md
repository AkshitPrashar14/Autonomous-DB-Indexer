# Autonomous Database Indexer (DBAutonomy)

## 📌 Project Overview
**DBAutonomy** is an intelligent, AI-driven database optimization platform that automatically detects slow-running PostgreSQL queries, analyzes table schemas, and proposes, evaluates, and safely deploys optimal database indexes without human intervention. 

By leveraging Large Language Models (Gemini/Groq) for index candidate generation and a Reinforcement Learning algorithm (LinUCB Contextual Bandit) for decision-making, the system continuously learns the most effective indexing strategies for any given database workload.

### ⚙️ How it Works
1. **Detection:** Monitors PostgreSQL logs (or pg_stat_statements) to identify slow-running queries.
2. **AI Parsing:** Parses complex SQL statements to extract key features (tables, WHERE clauses, JOINs) using deterministic fallback and LLMs.
3. **Candidate Generation:** Uses **Gemini** (or **Groq**) to analyze the schema and query, suggesting multiple index candidates (B-Tree, Hash, Composite).
4. **Bandit Selection:** A LinUCB Contextual Bandit selects the most promising index based on historical reward data, balancing exploration (trying new index types) and exploitation (using known good indexes).
5. **Shadow Testing:** Safely benchmarks the proposed index in a shadow database environment to measure actual latency improvements without impacting production.
6. **Safety Gate & Deployment:** Evaluates the benchmark results against strict safety thresholds (e.g., minimum latency improvement, write-regression limits) before deploying the index to production.

---

## 👨‍💻 My Contribution & Work Done
As the sole developer on this project, I built the entire application architecture from the ground up. My primary contributions include:

* **Core Architecture Design:** Designed the modular, asynchronous Python backend using FastAPI, Pydantic, and asyncio.
* **AI & RL Integration:** Implemented the prompt engineering for Gemini/Groq to generate valid SQL indexes, and built the mathematical implementation of the LinUCB Contextual Bandit for reward-based decision making.
* **Database Shadowing System:** Developed the `ShadowDatabaseManager` that replicates production schemas and executes safe `EXPLAIN ANALYZE` benchmarks without locking production tables.
* **Frontend Dashboard UI:** Built the interactive, real-time Streamlit dashboard that visualizes the pipeline's decision-making process step-by-step.
* **Cloud Deployment Pipeline:** Configured and deployed the architecture to Streamlit Community Cloud, overcoming complex dependency compilation challenges (e.g., `asyncpg` and `pydantic-core` C-extensions on Python 3.11).

*(Note: This was an individual project; there were no other team members.)*

---

## ✨ Key Features
* **Zero-Touch Optimization:** Fully autonomous pipeline from slow query detection to index deployment.
* **AI-Powered Generation:** Uses state-of-the-art LLMs (Gemini 3.6 Flash / Groq Llama 3.1) for intelligent index design.
* **Reinforcement Learning:** Implements LinUCB Contextual Bandits to learn which index types work best for specific query patterns over time.
* **Fail-Safe Shadow Testing:** Strictly isolates experimental indexes to a shadow database, ensuring zero risk to production stability.
* **Real-time Observability:** A beautiful Streamlit dashboard that provides a live event stream of the AI's internal thought process and mathematical decision-making.

---

## 🛠️ Technical Decisions
1. **Python / AsyncIO Framework:** Chosen for its rich ecosystem of AI/ML libraries and ability to handle high-concurrency database connections efficiently.
2. **Streamlit for the Dashboard:** Allowed for rapid prototyping of a complex, state-driven UI without needing a separate React/Vue frontend repository.
3. **Gemini / Groq LLMs:** Chosen for their exceptionally low latency, which is critical for real-time database optimization pipelines.
4. **LinUCB over standard Multi-Armed Bandits:** A Contextual Bandit was necessary because the "best" index type is highly dependent on the *context* (e.g., query type, table size, data cardinality).

---

## 🚧 Challenges Overcome
1. **LLM Hallucinations in SQL:** Initially, the LLMs would sometimes suggest syntactically invalid indexes or invent non-existent columns. I overcame this by developing a strict `SafetyGate` validation layer and robust Pydantic schemas to strictly enforce structural integrity before the Bandit even considers the candidate.
2. **Streamlit Cloud Deployment Issues:** Deploying the application to Streamlit Cloud initially failed due to build environment incompatibilities with `asyncpg` and Python 3.14. I solved this by explicitly locking the deployment to Python 3.11 and streamlining the `requirements.txt` to avoid local compilation of C-extensions.
3. **UI State Management in Streamlit:** Streamlit's top-down execution model made it difficult to show real-time, step-by-step progress of the async AI pipeline. I resolved this by decoupling the async execution loop and updating a localized `st.empty()` container dynamically.

---

## 🚀 Live Demo & Links
* **GitHub Repository:** [https://github.com/AkshitPrashar14/Autonomous-DB-Indexer](https://github.com/AkshitPrashar14/Autonomous-DB-Indexer)
* **Live Demo URL:** [https://autonomous-db-indexer.streamlit.app/](https://autonomous-db-indexer.streamlit.app/)

*(To use the live demo, click on one of the 'RUN' buttons under the Demo Controls section at the bottom of the page to watch the AI process a query in real-time.)*
