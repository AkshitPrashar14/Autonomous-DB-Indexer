import os
import sys

# Determine mode (check env var or if running in Streamlit Cloud's default mount path)
is_streamlit_demo = (
    os.environ.get("DEMO_MODE", "").lower() == "streamlit" 
    or "/mount/src/" in os.path.abspath(__file__)
)

if is_streamlit_demo:
    with open(os.path.join(os.path.dirname(__file__), "main_streamlit.py"), encoding="utf-8") as f:
        exec(f.read())
else:
    with open(os.path.join(os.path.dirname(__file__), "main_redis.py"), encoding="utf-8") as f:
        exec(f.read())
