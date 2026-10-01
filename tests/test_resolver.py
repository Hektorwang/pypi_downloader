"""Unit tests for DependencyResolver command building."""

from pathlib import Path

from pypi_downloader.resolver import DependencyResolver


class TestBuildCommand:
    def test_official_command(self) -> None:
        resolver = DependencyResolver(requirements_path=Path("requirements.txt"))
        cmd = resolver._build_command()
        assert cmd[:4] == [
            __import__("sys").executable,
            "-m",
            "piptools",
            "compile",
        ]
        assert "requirements.txt" in cmd
        assert "-o" in cmd
        assert "-" in cmd
        assert "--no-header" in cmd
        assert "-i" not in cmd

    def test_cn_command_uses_canonical_tuna_index(self) -> None:
        resolver = DependencyResolver(
            requirements_path=Path("requirements.txt"), use_cn_mirrors=True
        )
        cmd = resolver._build_command()
        index = cmd[cmd.index("-i") + 1]
        assert index == "https://pypi.tuna.tsinghua.edu.cn/simple"

    def test_extra_args_forwarded(self) -> None:
        resolver = DependencyResolver(
            requirements_path=Path("requirements.txt"),
            extra_args=["--generate-hashes", "--strip-extras"],
        )
        cmd = resolver._build_command()
        assert "--generate-hashes" in cmd
        assert "--strip-extras" in cmd
