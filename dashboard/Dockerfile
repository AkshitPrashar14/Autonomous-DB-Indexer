FROM python:3.11-slim

WORKDIR /dashboard
RUN pip install --no-cache-dir streamlit==1.38.0 requests==2.32.3 redis==5.0.8 google-generativeai openai
COPY . .

EXPOSE 8501
