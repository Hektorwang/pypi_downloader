"""Unit and integration tests for DependencyResolver (uv backend)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pypi_downloader.resolver import DependencyResolver

# The integration smoke test needs a real uv binary (and network), and is
# additionally gated behind PYPI_DOWNLOADER_INTEGRATION=1 so network-less
# environments never fail the suite.
_UV_AVAILABLE = (
    subprocess.run(
        [sys.executable, "-m", "uv", "--version"],
        capture_output=True,
    ).returncode
    == 0
)
_INTEGRATION_ENABLED = os.environ.get("PYPI_DOWNLOADER_INTEGRATION") == "1"


class TestBuildCommand:
    def test_official_command(self) -> None:
        resolver = DependencyResolver(requirements_path=Path("requirements.txt"))
        cmd = resolver._build_command()
        assert cmd[:6] == [
            sys.executable,
            "-m",
            "uv",
            "pip",
            "compile",
            "--universal",
        ]
        assert "--no-header" in cmd
        assert "requirements.txt" in cmd
        # Without --cn the index is uv's default; nothing is passed explicitly.
        assert "--index-url" not in cmd

    def test_cn_command_uses_canonical_tuna_index(self) -> None:
        resolver = DependencyResolver(
            requirements_path=Path("requirements.txt"), use_cn_mirrors=True
        )
        cmd = resolver._build_command()
        index = cmd[cmd.index("--index-url") + 1]
        assert index == "https://pypi.tuna.tsinghua.edu.cn/simple"

    def test_extra_args_forwarded(self) -> None:
        resolver = DependencyResolver(
            requirements_path=Path("requirements.txt"),
            extra_args=["--upgrade", "--no-annotate"],
        )
        cmd = resolver._build_command()
        assert "--upgrade" in cmd
        assert "--no-annotate" in cmd

    def test_generate_hashes_is_rejected(self) -> None:
        """--generate-hashes output cannot be parsed; reject it loudly."""
        resolver = DependencyResolver(
            requirements_path=Path("requirements.txt"),
            extra_args=["--generate-hashes"],
        )
        with pytest.raises(ValueError, match="--generate-hashes"):
            resolver.resolve()

    def test_no_piptools_references(self) -> None:
        resolver = DependencyResolver(requirements_path=Path("requirements.txt"))
        cmd = resolver._build_command()
        assert "piptools" not in cmd
        assert "pip-compile" not in " ".join(cmd)


@pytest.mark.integration
@pytest.mark.skipif(
    not (_UV_AVAILABLE and _INTEGRATION_ENABLED),
    reason="uv not available or PYPI_DOWNLOADER_INTEGRATION not set to 1",
)
class TestResolveIntegration:
    def test_resolve_real_package(self, tmp_path: Path) -> None:
        """A real `uv pip compile` run against PyPI for a tiny package."""
        requirements = tmp_path / "requirements.in"
        requirements.write_text("six==1.17.0\n")
        resolver = DependencyResolver(requirements_path=requirements)
        resolved = resolver.resolve()
        assert "six==1.17.0" in resolved

    def test_resolve_emits_platform_markers(self, tmp_path: Path) -> None:
        """Universal resolution must include Windows-only deps even on Linux."""
        requirements = tmp_path / "requirements.in"
        requirements.write_text("tqdm\n")
        resolver = DependencyResolver(requirements_path=requirements)
        resolved = resolver.resolve()
        # tqdm depends on colorama only on Windows; universal mode pins it
        # with a marker regardless of the host platform.
        assert "colorama==" in resolved
        assert "win32" in resolved
