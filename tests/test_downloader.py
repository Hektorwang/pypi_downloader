"""Unit tests for the pure helper logic of PackageDownloader."""

import pytest

from pypi_downloader.downloader import PackageDownloader


@pytest.fixture()
def downloader() -> PackageDownloader:
    return PackageDownloader(requirements_content="")


class TestParsePackageLine:
    def test_name_with_version(self) -> None:
        assert PackageDownloader.parse_package_line("zabbix-utils==3.1") == (
            "zabbix-utils",
            "3.1",
        )

    def test_extras_with_version(self) -> None:
        assert PackageDownloader.parse_package_line("requests[socks]==2.31.0") == (
            "requests[socks]",
            "2.31.0",
        )

    def test_name_without_version(self) -> None:
        assert PackageDownloader.parse_package_line("numpy") == ("numpy", "")

    def test_comment_and_empty_lines(self) -> None:
        assert PackageDownloader.parse_package_line("# comment") is None
        assert PackageDownloader.parse_package_line("") is None
        assert PackageDownloader.parse_package_line("   ") is None

    def test_environment_marker_is_stripped(self) -> None:
        assert PackageDownloader.parse_package_line(
            'numpy==1.26.4 ; python_version >= "3.8"'
        ) == ("numpy", "1.26.4")

    def test_non_pinned_operator_not_parsed(self) -> None:
        # pip-compile output is always ==-pinned; other specifiers are rejected.
        assert PackageDownloader.parse_package_line("numpy>=1.0") is None

    def test_hash_continuation_line_not_parsed(self) -> None:
        assert PackageDownloader.parse_package_line("    --hash=sha256:abc") is None


class TestParseWheelFilename:
    def test_basic_wheel(self) -> None:
        info = PackageDownloader.parse_wheel_filename(
            "numpy-1.26.4-cp311-cp311-manylinux_2_17_x86_64.whl"
        )
        assert info == {
            "name": "numpy",
            "version": "1.26.4",
            "build": None,
            "python": "cp311",
            "abi": "cp311",
            "platform": "manylinux_2_17_x86_64",
        }

    def test_wheel_with_build_tag(self) -> None:
        info = PackageDownloader.parse_wheel_filename("foo-1.0-1-py3-none-any.whl")
        assert info is not None
        assert info["build"] == "1"
        assert info["python"] == "py3"

    def test_non_wheel_returns_none(self) -> None:
        assert PackageDownloader.parse_wheel_filename("pkg-1.0.tar.gz") is None


class TestPython2Filter:
    def test_cp27_only_wheel_is_rejected(self, downloader: PackageDownloader) -> None:
        # Regression: cp2x-only wheels used to slip through the Python 2 filter.
        assert (
            downloader.matches_filter("old-1.0-cp27-cp27mu-manylinux1_x86_64.whl")
            is False
        )

    def test_py2_only_wheel_is_rejected(self, downloader: PackageDownloader) -> None:
        assert downloader.matches_filter("old-1.0-py2-none-any.whl") is False

    def test_universal_py2py3_wheel_passes(self, downloader: PackageDownloader) -> None:
        assert downloader.matches_filter("six-1.16.0-py2.py3-none-any.whl") is True

    def test_py3_wheel_passes(self, downloader: PackageDownloader) -> None:
        assert (
            downloader.matches_filter("pkg-1.0-cp311-cp311-win_amd64.whl") is True
        )

    def test_sdist_always_passes(self, downloader: PackageDownloader) -> None:
        assert downloader.matches_filter("pkg-1.0.tar.gz") is True


class TestFilters:
    def test_python_version_filter(self, downloader: PackageDownloader) -> None:
        wheel = "pkg-1.0-cp311-cp311-win_amd64.whl"
        assert downloader.matches_filter(wheel, python_version="cp311") is True
        assert downloader.matches_filter(wheel, python_version="cp39") is False

    def test_platform_filter(self, downloader: PackageDownloader) -> None:
        wheel = "pkg-1.0-cp311-cp311-win_amd64.whl"
        assert downloader.matches_filter(wheel, platform="win_amd64") is True
        assert downloader.matches_filter(wheel, platform="any") is False

    def test_abi_filter(self, downloader: PackageDownloader) -> None:
        wheel = "pkg-1.0-cp311-cp311-win_amd64.whl"
        assert downloader.matches_filter(wheel, abi="cp311") is True
        assert downloader.matches_filter(wheel, abi="none") is False


def _metadata_with(*releases: tuple[str, list[str]]) -> dict:
    """Build minimal PyPI metadata: releases map version -> list of filenames."""
    return {
        "releases": {
            version: [{"filename": name} for name in files]
            for version, files in releases
        }
    }


class TestVersionSelection:
    def test_find_all_python3_versions_excludes_py2_only(self) -> None:
        metadata = _metadata_with(
            ("1.0.0", ["old-1.0-cp27-cp27mu-manylinux1_x86_64.whl"]),
            ("2.0.0", ["new-2.0.0-py3-none-any.whl"]),
            ("2.0.1", ["new-2.0.1.tar.gz"]),
        )
        downloader = PackageDownloader(requirements_content="")
        result = downloader.find_all_python3_versions(metadata)
        assert set(result) == {"2.0.0", "2.0.1"}

    def test_filter_latest_patch_versions(self) -> None:
        metadata = _metadata_with(
            ("2.1.3", [{"filename": "a"}]),
            ("2.1.9", [{"filename": "b"}]),
            ("2.2.2", [{"filename": "c"}]),
            ("2.2.8", [{"filename": "d"}]),
            ("3.0.0", [{"filename": "e"}]),
        )
        downloader = PackageDownloader(requirements_content="")
        result = downloader.filter_latest_patch_versions(metadata["releases"])
        assert set(result) == {"2.1.9", "2.2.8", "3.0.0"}

    def test_count_downloadable_files(self) -> None:
        metadata = _metadata_with(
            ("1.0.0", ["pkg-1.0.0-py3-none-any.whl", "pkg-1.0.0.tar.gz"]),
        )
        downloader = PackageDownloader(requirements_content="")
        assert downloader._count_downloadable_files(metadata, "1.0.0") == 2


class TestRewriteUrl:
    OFFICIAL_URL = (
        "https://files.pythonhosted.org/packages/ab/cd/ef/pkg-1.0-py3-none-any.whl"
    )

    def test_official_passthrough(self, downloader: PackageDownloader) -> None:
        assert downloader.rewrite_url(self.OFFICIAL_URL) == self.OFFICIAL_URL

    def test_cn_rewrite(self) -> None:
        downloader = PackageDownloader(requirements_content="", use_cn_mirrors=True)
        idx = downloader._available_mirrors.index("https://mirrors.cernet.edu.cn/pypi")
        downloader._preferred_mirror_idx = idx
        rewritten = downloader.rewrite_url(self.OFFICIAL_URL)
        assert rewritten == (
            "https://mirrors.cernet.edu.cn/pypi/web/packages/ab/cd/ef/"
            "pkg-1.0-py3-none-any.whl"
        )

    def test_rewrite_is_idempotent(self) -> None:
        downloader = PackageDownloader(requirements_content="", use_cn_mirrors=True)
        once = downloader.rewrite_url(self.OFFICIAL_URL)
        # A second rewrite (e.g. inside download_file) must not double-rewrite.
        assert downloader.rewrite_url(once) == once

    def test_rewrite_url_plain_layout(self) -> None:
        """Cloud-vendor mirrors serve files without the TUNA-style "web/" prefix."""
        downloader = PackageDownloader(requirements_content="", use_cn_mirrors=True)
        expected = {
            "https://mirrors.cloud.tencent.com/pypi": (
                "https://mirrors.cloud.tencent.com/pypi/packages/"
            ),
            "https://mirrors.volces.com/pypi": "https://mirrors.volces.com/pypi/packages/",
        }
        for mirror, prefix in expected.items():
            downloader._preferred_mirror_idx = downloader._available_mirrors.index(mirror)
            rewritten = downloader.rewrite_url(self.OFFICIAL_URL)
            assert rewritten.startswith(prefix), mirror
            assert rewritten.endswith("pkg-1.0-py3-none-any.whl"), mirror

    def test_rewrite_url_huawei_layout_when_passed_explicitly(self) -> None:
        """Huawei Cloud left the built-in list but stays usable via --mirror."""
        downloader = PackageDownloader(requirements_content="", use_cn_mirrors=True)
        rewritten = downloader.rewrite_url(
            self.OFFICIAL_URL, "https://mirrors.huaweicloud.com/repository/pypi"
        )
        assert rewritten == (
            "https://mirrors.huaweicloud.com/repository/pypi/packages/"
            "ab/cd/ef/pkg-1.0-py3-none-any.whl"
        )

    def test_custom_mirror_uses_default_web_layout(self) -> None:
        downloader = PackageDownloader(
            requirements_content="", custom_mirrors=["https://m.internal/pypi"]
        )
        rewritten = downloader.rewrite_url(self.OFFICIAL_URL, "https://m.internal/pypi")
        assert rewritten == (
            "https://m.internal/pypi/web/packages/ab/cd/ef/pkg-1.0-py3-none-any.whl"
        )

    def test_mirror_base_has_no_double_slash(self) -> None:
        base = PackageDownloader._mirror_base(
            "https://mirrors.aliyun.com/pypi", "web/packages/"
        )
        assert base == "https://mirrors.aliyun.com/pypi/web/packages/"
        assert "//" not in base.removeprefix("https://")

    def test_custom_mirror_tried_first(self) -> None:
        downloader = PackageDownloader(
            requirements_content="",
            use_cn_mirrors=True,
            custom_mirrors=["https://m.internal/pypi"],
        )
        assert downloader._available_mirrors[0] == "https://m.internal/pypi"
        assert downloader._available_mirrors[-1] == PackageDownloader.OFFICIAL_PYPI

    def test_mirror_order_is_uniform_shuffle(self) -> None:
        downloader = PackageDownloader(requirements_content="", use_cn_mirrors=True)
        cn = downloader._available_mirrors[:-1]
        assert sorted(cn) == sorted(PackageDownloader.PYPI_MIRRORS)
        assert downloader._available_mirrors[-1] == PackageDownloader.OFFICIAL_PYPI
        assert len(downloader._available_mirrors) == 5  # 4 CN + official

    def test_custom_mirror_alone(self) -> None:
        downloader = PackageDownloader(
            requirements_content="",
            custom_mirrors=["https://m.internal/pypi"],
        )
        assert downloader._available_mirrors == [
            "https://m.internal/pypi",
            PackageDownloader.OFFICIAL_PYPI,
        ]


class TestMisc:
    def test_cleanup_stale_part_files(self, tmp_path) -> None:
        (tmp_path / "pkg-1.0.whl.part").write_bytes(b"junk")
        (tmp_path / "other.whl").write_bytes(b"keep")
        downloader = PackageDownloader(requirements_content="", download_dir=tmp_path)
        removed = downloader._cleanup_stale_part_files()
        assert removed == 1
        assert not (tmp_path / "pkg-1.0.whl.part").exists()
        assert (tmp_path / "other.whl").exists()

    def test_compute_hash(self, tmp_path) -> None:
        target = tmp_path / "file.bin"
        target.write_bytes(b"hello world")
        digest = PackageDownloader.compute_hash(target)
        assert digest.startswith("b94d27b9934d3e08a52e52d7da7dabfa")

    def test_hash_value_extraction(self) -> None:
        assert PackageDownloader._hash_value("sha256=abc123") == "abc123"
        assert PackageDownloader._hash_value("abc123") == "abc123"
        assert PackageDownloader._hash_value(None) is None

    def test_parse_valid_lines(self) -> None:
        downloader = PackageDownloader(
            requirements_content=(
                "# comment\n"
                "six==1.16.0\n"
                " \n"
                "requests[socks]==2.31.0 ; python_version >= '3.8'\n"
                "not a package line!!!\n"
            )
        )
        triples = downloader._parse_valid_lines()
        assert [(name, version) for _line, name, version in triples] == [
            ("six", "1.16.0"),
            ("requests[socks]", "2.31.0"),
        ]
