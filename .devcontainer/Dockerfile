FROM python:3.10.13-slim

# Install curl, git and Claude Code via official install script
RUN apt-get update && apt-get install -y --no-install-recommends curl git ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    curl -fsSL https://claude.ai/install.sh | bash

ENV PATH="/root/.local/bin:$PATH"
