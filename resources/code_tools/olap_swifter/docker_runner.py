"""
Lightweight Docker runner for tools requiring containerized execution
(e.g., Tesseract OCR system binaries or isolated Presidio instances).
"""

import subprocess
import shutil
from pathlib import Path
from typing import List, Optional, Dict, Any


class DockerRunner:
    """Helper to run tool commands inside the olap-swifter container when needed."""

    def __init__(self, image_tag: str = "olap-swifter:latest", workspace_dir: Optional[Path] = None):
        self.image_tag = image_tag
        self.workspace_dir = workspace_dir or Path.cwd()

    def is_docker_available(self) -> bool:
        """Check if Docker CLI is available on host."""
        return shutil.which("docker") is not None

    def build_image(self) -> subprocess.CompletedProcess:
        """Build the lightweight tool container."""
        if not self.is_docker_available():
            raise RuntimeError("Docker is not installed or not available in PATH.")

        cmd = [
            "docker", "build",
            "-t", self.image_tag,
            str(self.workspace_dir)
        ]
        return subprocess.run(cmd, capture_output=True, text=True, check=True)

    def run_command(self, cmd_args: List[str], env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
        """Run an arbitrary command inside the container with workspace mounted."""
        if not self.is_docker_available():
            raise RuntimeError("Docker is not installed or not available in PATH.")

        docker_cmd = [
            "docker", "run", "--rm",
            "-v", f"{self.workspace_dir}:/app",
            "-w", "/app"
        ]

        if env:
            for k, v in env.items():
                docker_cmd.extend(["-e", f"{k}={v}"])

        docker_cmd.append(self.image_tag)
        docker_cmd.extend(cmd_args)

        return subprocess.run(docker_cmd, capture_output=True, text=True)
