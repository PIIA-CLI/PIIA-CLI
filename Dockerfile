FROM python:3.12-slim

ARG REPOWISE_COMMIT=8b58b52dcaa8aa221f00cd75ac06d91421f0376b

LABEL org.opencontainers.image.source="https://github.com/PIIA-CLI/PIIA-CLI"
LABEL org.opencontainers.image.licenses="AGPL-3.0-or-later"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY . /opt/piia-cli
RUN python -m pip install --no-cache-dir /opt/piia-cli \
    && python -m pip install --no-cache-dir \
      "repowise @ git+https://github.com/ithllc/repowise.git@${REPOWISE_COMMIT}"

WORKDIR /github/workspace
ENTRYPOINT ["python", "-m", "piia.automation.action"]
