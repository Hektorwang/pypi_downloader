"""
resolver.py - Dependency resolution module using pip-compile.

Encapsulates all pip-compile interactions for resolving transitive
dependencies from a requirements file.
"""

import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from loguru import logger


class DependencyResolver:
    """
    Resolves Python package dependencies using pip-compile.

    Wraps pip-compile (from pip-tools) to produce a fully-pinned,
    transitive dependency list from a loose requirements file.
    The resolved output is returned as an in-memory string to avoid
    unnecessary file system writes.
    """

    DEFAULT_INDEX_URL: str = "https://pypi.org/simple"
    CN_INDEX_URL: str = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"

    def __init__(
        self,
        requirements_path: Path,
        use_cn_mirrors: bool = False,
        extra_args: Optional[List[str]] = None,
    ) -> None:
        """
        Initialize the DependencyResolver.

        Args:
            requirements_path: Path to the input requirements file.
            use_cn_mirrors: If True, use a Chinese mirror for resolution.
            extra_args: Additional arguments forwarded verbatim to pip-compile.
        """
        self.requirements_path: Path = requirements_path
        self.use_cn_mirrors: bool = use_cn_mirrors
        self.extra_args: List[str] = extra_args or []

    def _build_command(self) -> List[str]:
        """
        Build the pip-compile command list.

        Returns:
            List of command tokens ready for subprocess.
        """
        cmd: List[str] = [
            sys.executable,
            "-m",
            "piptools",
            "compile",
            str(self.requirements_path),
            "-o",
            "-",  # Output resolved content to stdout
            "--no-header",
        ]

        if self.use_cn_mirrors:
            cmd.extend(["-i", self.CN_INDEX_URL])
            logger.info(f"Using Chinese mirror for resolution: {self.CN_INDEX_URL}")
        else:
            logger.info("Using official PyPI for dependency resolution")

        cmd.extend(self.extra_args)
        return cmd

    def resolve(self) -> str:
        """
        Run pip-compile and return the resolved requirements as a string.

        Raises:
            FileNotFoundError: If pip-compile / pip-tools is not installed.
            subprocess.CalledProcessError: If pip-compile exits with a non-zero code.
            RuntimeError: For any other unexpected failure.

        Returns:
            Resolved requirements content as a multi-line string.
        """
        cmd = self._build_command()

        logger.info("=" * 60)
        logger.info("Resolving dependencies with pip-compile...")
        logger.info("=" * 60)
        logger.info(f"Input file: {self.requirements_path}")
        logger.info(f"Running command: {' '.join(cmd)}")
        logger.info("This may take a while depending on the number of packages...")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError as exc:
            logger.error("=" * 60)
            logger.error("pip-compile command not found!")
            logger.error("Please install pip-tools: pip install pip-tools")
            logger.error("=" * 60)
            raise FileNotFoundError(
                "pip-compile not found. Install pip-tools: pip install pip-tools"
            ) from exc
        except subprocess.CalledProcessError as exc:
            logger.error("=" * 60)
            logger.error("Failed to resolve dependencies!")
            logger.error(f"Error: {exc.stderr}")
            logger.error("=" * 60)
            raise

        resolved_content: str = result.stdout

        # Log pip-compile stderr at debug level (warnings / informational output)
        if result.stderr:
            for line in result.stderr.strip().splitlines():
                logger.debug(f"  pip-compile: {line}")

        resolved_lines: List[str] = [
            line
            for line in resolved_content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        logger.info("=" * 60)
        logger.info("Dependencies resolved successfully!")
        logger.info(f"Resolved {len(resolved_lines)} packages in memory")
        logger.info("=" * 60)

        return resolved_content
