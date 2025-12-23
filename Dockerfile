FROM ghcr.io/astral-sh/uv:alpine3.22

# install bash support for tooling
RUN apk update && apk add bash curl shadow gosu sed

# Create vibeuser with a default UID/GID that can be overridden at runtime
ARG VIBE_UID=1000
ARG VIBE_GID=1000
RUN addgroup -g ${VIBE_GID} vibeuser && \
    adduser -u ${VIBE_UID} -G vibeuser -D vibeuser

RUN mkdir -p /app && chown vibeuser:vibeuser /app
RUN mkdir -p /logs && chown vibeuser:vibeuser /logs
RUN mkdir -p /vibe && chown vibeuser:vibeuser /vibe
RUN mkdir -p /work && chown vibeuser:vibeuser /work

# copy source code (excluding hidden files and directories)
USER vibeuser
WORKDIR /app
COPY --chown=vibeuser:vibeuser . /app/

# build vibe and install it in development mode as vibeuser
RUN uv sync --python 3.12
ENV VIBE_HOME="/vibe"

# Set the entrypoint (entrypoint bootstraps vibe with correct permissions)
USER root
WORKDIR /work
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["/app/.venv/bin/vibe"]
