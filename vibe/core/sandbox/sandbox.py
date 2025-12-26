from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import uuid

import docker
from docker.errors import DockerException, ImageNotFound
from rich import print as rprint

from vibe.core.paths.global_paths import _get_vibe_home
from vibe.core.utils import logger

IMAGE_NAME = "mistral-vibe"


def run_sandbox(cwd: str, arg_str: str) -> None:
    """Launch vibe in a Docker container sandbox.

    Args:
        cwd: Current working directory to mount into the container.
        arg_str: Additional arguments to append to the container command.

    Raises:
        SystemExit: If Docker is not available or container launch fails.
    """
    # Convert cwd to absolute path for Docker volume mount
    cwd = str(Path(cwd).resolve())

    try:
        client = docker.from_env()
    except DockerException as e:
        rprint("[red]Error: Docker is not available or not running.[/]")
        raise SystemExit(1) from e
    except Exception as e:
        rprint("[red]Error: Failed to initialize Docker client.[/]")
        raise SystemExit(1) from e

    container_name = f"mistral-vibe-{uuid.uuid4()}"

    # Check for required environment and config
    api_key = os.environ.get("MISTRAL_API_KEY")
    vibe_dir = _get_vibe_home()

    if not api_key and not vibe_dir.exists():
        rprint("[red]Error: Mistral-vibe is not initialized.[/]")
        rprint("[yellow]Please run mistral-vibe on the host first to set up your API key and configuration.[/]")
        raise SystemExit(1) from None

    # Build volume mount arguments
    volume_args = [f"-v{cwd}:/work"]
    if vibe_dir.exists():
        volume_args.append(f"-v{vibe_dir}:/vibe_in:ro")
        logger.info(f"Mounting {vibe_dir} to /vibe_in")

    # Build environment arguments
    env_args = []
    if api_key:
        env_args.append(f"-eMISTRAL_API_KEY={api_key}")
        logger.info("Passing MISTRAL_API_KEY to container")

    try:
        try:
            image = client.images.get(IMAGE_NAME)
        except ImageNotFound:
            rprint(f"[yellow]Image '{IMAGE_NAME}' not found. Build it with scripts/container.sh[/]")
            raise SystemExit(1) from None

        cmd = image.attrs["Config"]["Cmd"]
        if cmd is None:
            cmd = []
        if not isinstance(cmd, list):
            cmd = [cmd]

        # Append arg_str to the command
        full_cmd = cmd + arg_str.split()

        # Build docker CLI command for interactive execution
        docker_cmd = [
            "docker",
            "run",
            "--rm",
            f"--name={container_name}",
            "--network=host",
            "-i",
        ]

        # Enables interactive sessions
        if sys.stdin.isatty():
            docker_cmd.append("-t")

        # Run container as current user to ensure proper file permissions
        # This ensures files created in bind mounts are owned by the host user
        uid = os.getuid()
        gid = os.getgid()
        docker_cmd.extend([f"-ePUID={uid}", f"-ePGID={gid}"])

        docker_cmd.extend([
            *volume_args,
            *env_args,
            IMAGE_NAME,
            *full_cmd,  # Pass additional arguments after the image name
        ])

        # Subprocess enables Ctrl+C-handling and stdin/stdout handling
        result = subprocess.run(docker_cmd)
        raise SystemExit(result.returncode)

    except SystemExit:
        raise
    except Exception as e:
        rprint(f"[red]Error: An unexpected error occurred while running the sandbox: {e}.[/]")
        raise SystemExit(1) from e
