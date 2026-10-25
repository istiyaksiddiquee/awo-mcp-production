FROM python:3.11-slim

# HF Spaces runs containers as uid 1000
RUN useradd -m -u 1000 user
USER user
ENV PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    MCP_TRANSPORT=streamable-http \
    HOST=0.0.0.0 \
    PORT=7860
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user server.py .
COPY --chown=user src ./src
COPY --chown=user sparql_queries ./sparql_queries

EXPOSE 7860
CMD ["python", "server.py"]