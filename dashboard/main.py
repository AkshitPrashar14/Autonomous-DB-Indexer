import os
import sys

# Determine mode
is_streamlit_demo = os.environ.get("DEMO_MODE", "").lower() == "streamlit"

if is_streamlit_demo:
    with open(os.path.join(os.path.dirname(__file__), "main_streamlit.py"), encoding="utf-8") as f:
        exec(f.read())
else:
    with open(os.path.join(os.path.dirname(__file__), "main_redis.py"), encoding="utf-8") as f:
        exec(f.read())
