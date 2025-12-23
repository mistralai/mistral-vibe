#!/bin/bash

# Build the Docker image with optional UID/GID arguments
# Usage: ./build_sandbox.sh [VIBE_UID] [VIBE_GID]

docker build \
  --build-arg VIBE_UID=${1:-1000} \
  --build-arg VIBE_GID=${2:-1000} \
  -t mistral-vibe \
  -f Dockerfile \
  .
