FROM python:3.10.13-slim

# Install curl and Claude Code via official install script
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/* && \
    curl -fsSL https://claude.ai/install.sh | bash

ENV PATH="/root/.local/bin:$PATH"
