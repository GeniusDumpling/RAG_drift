# Add browser collection dependencies to an existing application image.
# Build from the project root; BASE_IMAGE can be the currently deployed image.
ARG BASE_IMAGE=intelligence-rag-app:latest
FROM ${BASE_IMAGE}
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN python -m pip install --no-cache-dir playwright==1.62.0 trafilatura==2.2.0 \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
COPY tools ./tools
