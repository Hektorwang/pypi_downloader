"""Dependency resolution module using uv.

Encapsulates the ``uv pip compile`` invocation that resolves transitive
dependencies from a requirements file into a fully-pinned list.

Resolution is *universal* (``--universal``): a single run covers all platforms
(Windows / macOS / Linux) at once, and platform-specific dependencies are
emitted with environment markers, e.g.::

    colorama==0.4.6 ; sys_platform == 'win32'

The downloader strips the marker part of every line, so the mirror ends up
with the union of all platforms' dependencies — exactly what an offline
multi-OS mirror needs. This replaces the previous pip-compile backend, which
could only resolve for the interpreter it ran on.

Note: uv does not read pip.conf; the index is always passed explicitly
(official PyPI by default, the Tsinghua mirror with ``--cn``). End users
installing from the built mirror are unaffected — the downloaded artifacts
are ordinary wheels and sdists.
"""

import subprocess
import sys
from pathlib import Path

from loguru import logger


class DependencyResolver:
    """Resolve Python package dependencies using ``uv pip compile``.

    Wraps uv (invoked via ``python -m uv`` so the binary always matches the
    declared dependency) to produce a fully-pinned, transitive dependency
    list from a loose requirements file. The resolved output is returned as
    an in-memory string to avoid unnecessary file system writes.
    """

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
            extra_args: Additional arguments forwarded verbatim to
                ``uv pip compile`` (e.g. ``--generate-hashes``).
            timeout_seconds: Maximum wall-clock time for the uv run.
        """
        self.requirements_path = requirements_path
        self.use_cn_mirrors = use_cn_mirrors
        self.extra_args: list[str] = extra_args or []
        self.timeout_seconds = timeout_seconds

    def _build_command(self) -> list[str]:
        """Build the ``uv pip compile`` command list.

        Runs uv via ``python -m uv`` so the resolver always uses the uv
        installed with this package, never an unrelated binary from PATH.
        Resolution is universal (all platforms in one pin); uv prints the
        resolved requirements to stdout by default.

        Returns:
            List of command tokens ready for :func:`subprocess.run`.
        """
        cmd: list[str] = [
            sys.executable,
            "-m",
            "uv",
            "pip",
            "compile",
            "--universal",  # One pin covering Windows / macOS / Linux
            "--no-header",
            str(self.requirements_path),
        ]

        if self.use_cn_mirrors:
            cmd.extend(["--index-url", self.CN_INDEX_URL])
            logger.info(f"Using Chinese mirror for resolution: {self.CN_INDEX_URL}")
        else:
            logger.info("Using official PyPI for dependency resolution")

        cmd.extend(self.extra_args)
        return cmd

    def resolve(self) -> str:
        """Run uv and return the resolved requirements as a string.

        Raises:
            ValueError: If extra_args contains ``--generate-hashes`` (hash
                annotated output cannot be parsed; package hashes come from
                the PyPI JSON API at download time instead).
            FileNotFoundError: If the uv dependency is not importable/usable.
            subprocess.CalledProcessError: If uv exits with a non-zero code
                (resolution failure — e.g. a graph universal resolution cannot
                satisfy; running the tool on each target platform is the
                documented fallback in that case).
            RuntimeError: If uv exceeds the timeout.
        """
        if "--generate-hashes" in self.extra_args:
            raise ValueError(
                "--generate-hashes is not supported: hash-annotated output "
                "cannot be parsed into package pins. Package hashes come from "
                "the PyPI JSON API at download time instead."
            )

        cmd = self._build_command()

        logger.info("=" * 60)
        logger.info("Resolving dependencies with uv (universal mode)...")
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
            logger.error("uv executable not found!")
            logger.error("Please install uv: pip install uv")
            raise FileNotFoundError("uv not found. Install uv: pip install uv") from exc
        except subprocess.TimeoutExpired as exc:
            logger.error(
                f"uv timed out after {self.timeout_seconds}s; "
                "increase the timeout or reduce the input size."
            )
            raise RuntimeError(
                f"uv timed out after {self.timeout_seconds}s"
            ) from exc
        except subprocess.CalledProcessError as exc:
            logger.error("Failed to resolve dependencies!")
            logger.error(f"Error: {exc.stderr}")
            logger.error(
                "Hint: universal resolution is stricter than per-platform "
                "resolution. If it cannot satisfy the dependency graph, run "
                "this tool once per target platform instead."
            )
            raise

        resolved_content: str = result.stdout

        # Log uv's stderr at debug level (the "Resolved N packages" summary).
        if result.stderr:
            for line in result.stderr.strip().splitlines():
                logger.debug(f"  uv: {line}")

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
