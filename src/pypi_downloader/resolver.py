"""Dependency resolution module using pip-compile.

Encapsulates all pip-compile interactions for resolving transitive
dependencies from a requirements file.

Note: pip-compile resolves for the *current* interpreter and platform, so
dependencies guarded by environment markers that do not match the host (e.g.
Windows-only packages resolved on Linux) are absent from the pin. See the
"Known Limitations" section of the README.
"""

import subprocess
import sys
from pathlib import Path

from loguru import logger


class DependencyResolver:
    """Resolve Python package dependencies using pip-compile.

    Wraps pip-compile (from pip-tools) to produce a fully-pinned, transitive
    dependency list from a loose requirements file. The resolved output is
    returned as an in-memory string to avoid unnecessary file system writes.
    """

    DEFAULT_INDEX_URL: str = "https://pypi.org/simple"
    DEFAULT_TIMEOUT_SECONDS: float = 1800.0
    CN_INDEX_URL: str = "https://pypi.tuna.tsinghua.edu.cn/simple"

    def __init__(
        self,
        requirements_path: Path,
        use_cn_mirrors: bool = False,
        extra_args: list[str] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """
        Initialize the DependencyResolver.

        Args:
            requirements_path: Path to the input requirements file.
            use_cn_mirrors: If True, use the canonical Tsinghua mirror for resolution.
            extra_args: Additional arguments forwarded verbatim to pip-compile.
            timeout_seconds: Maximum wall-clock time for the pip-compile run.
        """
        self.requirements_path = requirements_path
        self.use_cn_mirrors = use_cn_mirrors
        self.extra_args: list[str] = extra_args or []
        self.timeout_seconds = timeout_seconds

    def _build_command(self) -> list[str]:
        """Build the pip-compile command list.

        Runs pip-compile via ``python -m piptools`` with the current
        interpreter, writes the pin to stdout, and appends the configured
        index URL plus any user-provided extra arguments.

        Returns:
            List of command tokens ready for :func:`subprocess.run`.
        """
        cmd: list[str] = [
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
        """Run pip-compile and return the resolved requirements as a string.

        Raises:
            FileNotFoundError: If pip-tools is not installed.
            subprocess.CalledProcessError: If pip-compile exits with a non-zero code.
            RuntimeError: If pip-compile exceeds the timeout.
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
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            logger.error("pip-compile command not found!")
            logger.error("Please install pip-tools: pip install pip-tools")
            raise FileNotFoundError(
                "pip-compile not found. Install pip-tools: pip install pip-tools"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            logger.error(
                f"pip-compile timed out after {self.timeout_seconds}s; "
                "increase the timeout or reduce the input size."
            )
            raise RuntimeError(
                f"pip-compile timed out after {self.timeout_seconds}s"
            ) from exc
        except subprocess.CalledProcessError as exc:
            logger.error("Failed to resolve dependencies!")
            logger.error(f"Error: {exc.stderr}")
            raise

        resolved_content: str = result.stdout

        # Log pip-compile stderr at debug level (warnings / informational output)
        if result.stderr:
            for line in result.stderr.strip().splitlines():
                logger.debug(f"  pip-compile: {line}")

        resolved_lines = [
            line
            for line in resolved_content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        logger.info("=" * 60)
        logger.info("Dependencies resolved successfully!")
        logger.info(f"Resolved {len(resolved_lines)} packages in memory")
        logger.info("=" * 60)

        return resolved_content
