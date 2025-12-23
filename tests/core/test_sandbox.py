from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import docker
import pytest

from vibe.core.sandbox.sandbox import IMAGE_NAME, run_sandbox

def _is_running_in_docker() -> bool:
    try:
        with open("/proc/1/cgroup", "rt", encoding="utf-8") as f:
            return "docker" in f.read()
    except FileNotFoundError:
        # Not running on Linux or /proc not available
        return False


def _docker_available() -> bool:
    # Skip if we're already running in a Docker container
    if _is_running_in_docker():
        return False

    try:
        client = docker.from_env()
        client.ping()
        return True
    except (docker.errors.DockerException, OSError):
        return False


def _image_available() -> bool:
    if not _docker_available():
        return False

    try:
        client = docker.from_env()
        client.images.get(IMAGE_NAME)
        return True
    except docker.errors.ImageNotFound:
        return False
    except docker.errors.DockerException:
        return False


class TestSandbox:
    def test_run_sandbox_requires_docker(self, tmp_path: Path) -> None:
        """Test that run_sandbox fails gracefully when Docker is not available."""
        with patch("docker.from_env") as mock_docker:
            mock_docker.side_effect = docker.errors.DockerException("Docker not available")

            with pytest.raises(SystemExit) as exc_info:
                run_sandbox(str(tmp_path), "")

            assert exc_info.value.code == 1

    def test_run_sandbox_requires_image(self, tmp_path: Path) -> None:
        """Test that run_sandbox fails gracefully when the required image is not available."""
        with patch("docker.from_env") as mock_docker:
            mock_client = mock_docker.return_value
            mock_client.images.get.side_effect = docker.errors.ImageNotFound("Image not found")

            with pytest.raises(SystemExit) as exc_info:
                run_sandbox(str(tmp_path), "")

            assert exc_info.value.code == 1

    @pytest.mark.skipif(
        not _docker_available(),
        reason="Docker is not installed or not running"
    )
    @pytest.mark.skipif(
        not _image_available(),
        reason=f"Docker image '{IMAGE_NAME}' is not available"
    )
    def test_run_sandbox_launches_container(self, tmp_path: Path) -> None:
        """Test that run_sandbox successfully launches a Docker container."""
        # Create a test file in the temporary directory
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello from sandbox test!", encoding="utf-8")

        # Test with a simple command that should exit quickly
        # We'll use a timeout to prevent hanging
        import threading

        # Store exceptions
        exception_ref = []

        def run_with_timeout():
            try:
                # This should launch the container and exit quickly with --help
                run_sandbox(str(tmp_path), "--help")
            except SystemExit as e:
                # Expected - the container will exit with code 0
                exception_ref.append(e)
            except Exception as e:
                exception_ref.append(e)

        # Start the sandbox in a separate thread
        thread = threading.Thread(target=run_with_timeout)
        thread.start()

        # Wait for the container to start and exit (should be quick with --help)
        thread.join(timeout=30)

        # Check if we got the expected SystemExit with code 0
        if exception_ref:
            if isinstance(exception_ref[0], SystemExit):
                assert exception_ref[0].code == 0
            else:
                raise exception_ref[0]
        else:
            # If no exception, the thread should have completed
            if thread.is_alive():
                pytest.fail("Sandbox container did not exit within expected time")
