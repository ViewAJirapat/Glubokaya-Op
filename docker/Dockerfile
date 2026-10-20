# Sandbox image for targets with sandbox = "docker" (see swarm/sandbox.py).
# Build with `uv run swarm docker-build`, which passes the harness venv's pytest/ruff/mypy
# versions so gates behave the same inside and outside the sandbox.
FROM python:3.12-slim

ARG AIDER_VERSION=0.86.2
ARG PYTEST_VERSION=9.1.1
ARG RUFF_VERSION=0.17.1
ARG MYPY_VERSION=2.4.0

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

# Two venvs: code under test must not import aider's dependencies, or a change could pass
# here and fail on a machine that only has the gate toolchain.
RUN python -m venv /opt/gates \
 && /opt/gates/bin/pip install \
      "pytest==${PYTEST_VERSION}" "ruff==${RUFF_VERSION}" "mypy==${MYPY_VERSION}"
RUN python -m venv /opt/aider \
 && /opt/aider/bin/pip install "aider-chat==${AIDER_VERSION}" \
 && ln -s /opt/aider/bin/aider /usr/local/bin/aider

# Bind-mounted files show up owned by another uid; git would refuse them as "dubious".
# The identity only matters if an agent commits inside the container: those commits land
# in a throwaway git dir and are never seen by the host repo.
RUN git config --system safe.directory '*' \
 && git config --system user.name swarm-agent \
 && git config --system user.email swarm-agent@localhost \
 && git config --system gc.auto 0 \
 && useradd --uid 1000 --no-create-home --home-dir /tmp agent

USER 1000:1000
WORKDIR /work
