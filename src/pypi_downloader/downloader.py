"""Core package downloader: metadata fetching, mirror fallback, streaming downloads.

Execution model (two phases):

1. Metadata phase — package metadata is fetched concurrently, mirror-first:
   several CN mirrors proxy the PyPI JSON API under a layout-dependent path
   (``{mirror}/web/json/`` or ``{mirror}/json/``), mirrors without the
   endpoint are skipped, and the official API sits at the end of the cycle as
   the final authority and fallback (its 404 cleanly identifies "package does
   not exist"). Results are cached, so each package is fetched exactly once
   even though phase 1 (counting) and phase 2 (downloading) both need it.
2. Download phase — every file is downloaded concurrently. Each attempt
   rewrites the canonical ``files.pythonhosted.org`` URL onto the mirror being
   tried, so switching mirrors always changes the URL actually fetched.
   Content streams to a ``.part`` file (1 MiB chunks) and is renamed into
   place atomically after the SHA-256 check passes.
"""

import asyncio
import errno
import hashlib
import json
import os
import posixpath
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import aiohttp
from loguru import logger
from packaging.version import InvalidVersion, Version

from pypi_downloader.ui import RichLogSink


class LocalIOFatalError(Exception):
    """Raised when a local I/O error makes further retries pointless.

    Retrying with a different mirror cannot resolve local storage or
    filesystem problems. Subclass this to represent specific conditions.

    Attributes:
        errno_code: The OS errno value that triggered this error, if available.
    """

    def __init__(self, message: str, errno_code: int | None = None) -> None:
        """Create the error.

        Args:
            message: Human-readable description of the failure.
            errno_code: The OS errno value, if the failure originated from an
                OSError; None otherwise.
        """
        super().__init__(message)
        self.errno_code = errno_code


class DiskFullError(LocalIOFatalError):
    """Raised when the disk is full or a disk quota is exceeded (ENOSPC / EDQUOT)."""


class _HashMismatchError(Exception):
    """Downloaded content does not match the expected SHA-256 digest.

    Internal control-flow exception: treated like any other attempt failure so
    the downloader retries the file from the next mirror.
    """


# errno codes that indicate a fatal local filesystem condition.
# Retrying with a different mirror cannot fix these. Add new codes here to
# extend coverage without touching exception-handling logic.
_FATAL_LOCAL_ERRNO: frozenset[int] = frozenset(
    {
        errno.ENOSPC,  # No space left on device
        errno.EDQUOT,  # Disk quota exceeded
        errno.EROFS,  # Read-only file system
    }
)


class PackageDownloader:
    """Download Python packages from multiple PyPI mirrors with fallback.

    Parses a resolved requirements list, fetches package metadata mirror-first
    (official PyPI JSON API as final authority and fallback; cached,
    concurrent), and downloads files with per-file concurrency control,
    streaming writes, hash verification, and automatic mirror switching on
    failure.
    """

    # Built-in CN mirrors, all large commercial vendors plus the CERNET
    # aggregator, all HTTPS (plain HTTP would undermine the SHA-256 check,
    # since the hashes themselves come from metadata served over the same
    # connection). All four proxy the PyPI JSON API, so any of them can serve
    # both metadata and package files. Huawei Cloud was evaluated and dropped
    # from this list (2026-10): it has no JSON metadata endpoint; it remains
    # usable via ``--mirror`` thanks to the layout mapping below.
    #
    # Individual university mirrors (TUNA, USTC, SJTU, ...) are deliberately
    # NOT listed: mirrors.cernet.edu.cn is the CERNET joint mirror — a
    # MirrorZ-based aggregator that auto-redirects to the participating
    # university mirror closest to the current network — so listing them
    # separately only duplicates what CERNET already routes to. Mirrors that
    # failed verification (163, ISCAS) or are no longer maintained (Baidu,
    # Qiniu, Douban) are omitted as well.
    PYPI_MIRRORS: list[str] = [
        "https://mirrors.aliyun.com/pypi",
        "https://mirrors.cloud.tencent.com/pypi",
        "https://mirrors.cernet.edu.cn/pypi",
        "https://mirrors.volces.com/pypi",
    ]

    # Per-mirror file layout. TUNA-style mirrors (aliyun, cernet) serve
    # package files under a "web/" prefix, while cloud-vendor mirrors (tencent,
    # volces, and huawei when passed via --mirror) serve them directly. Mirrors
    # absent from this dict use the TUNA-style default (custom --mirror URLs,
    # too). The JSON metadata endpoint is derived from the same prefix
    # ("web/json/" vs "json/").
    _MIRROR_FILE_PREFIX = "web/packages/"
    _MIRROR_FILE_PREFIXES: dict[str, str] = {
        "https://mirrors.cloud.tencent.com/pypi": "packages/",
        "https://mirrors.huaweicloud.com/repository/pypi": "packages/",
        "https://mirrors.volces.com/pypi": "packages/",
    }

    OFFICIAL_PYPI = "https://pypi.org"
    PYPI_JSON_API = "https://pypi.org/pypi"

    DEFAULT_CONCURRENCY: int = 16
    # Total attempts per file: 5 sites * 2 per site = 10 per cycle, ~3 cycles.
    DEFAULT_RETRIES: int = 32
    RETRIES_PER_MIRROR: int = 2  # Attempts per mirror before switching
    DOWNLOAD_CHUNK_SIZE: int = 1024 * 1024  # 1 MiB streamed per chunk

    def __init__(
        self,
        requirements_content: str,
        dry_run: bool = False,
        concurrency: int = DEFAULT_CONCURRENCY,
        download_dir: Path | None = None,
        use_cn_mirrors: bool = False,
        custom_mirrors: list[str] | None = None,
        python_version: str | None = None,
        abi: str | None = None,
        platform: str | None = None,
        all_versions: bool = False,
        latest_patch: bool = False,
        url_list_path: Path | None = None,
    ) -> None:
        """Initialize the PackageDownloader.

        Args:
            requirements_content: Resolved dependencies as a string.
            dry_run: If True, only generate a URL list without downloading.
            concurrency: Maximum number of concurrent file downloads.
            download_dir: Directory to save downloaded packages
                (defaults to ``./pypi``).
            use_cn_mirrors: If True, use the built-in Chinese mirrors with fallback.
            custom_mirrors: Extra mirror base URLs tried before the built-in list.
            python_version: Python version filter (e.g., "cp311", "py3").
            abi: ABI filter (e.g., "cp311", "abi3", "none").
            platform: Platform filter (e.g., "manylinux_2_17_x86_64", "win_amd64").
            all_versions: If True, download all available versions of each
                package (Python 3 only).
            latest_patch: If True, download only the latest patch version for
                each minor version.
            url_list_path: Path to save the URL list (defaults to
                ``./url_list.txt``; only used in dry-run mode).
        """
        self.requirements_content = requirements_content
        self.session: aiohttp.ClientSession | None = None
        self.dry_run = dry_run
        # Bounds concurrent file downloads (acquired per file, not per package).
        self.semaphore = asyncio.Semaphore(concurrency)
        # Metadata responses can be tens of MB for popular packages, so keep
        # fewer of them in flight than file downloads.
        self._metadata_semaphore = asyncio.Semaphore(min(concurrency, 8))
        self._metadata_cache: dict[str, dict[str, Any] | None] = {}
        self.download_urls: list[str] = []
        self.download_dir = download_dir if download_dir is not None else Path.cwd() / "pypi"
        self.url_list_file = (
            url_list_path if url_list_path else Path.cwd() / "url_list.txt"
        )
        # total=None: large wheels must not be killed mid-transfer. connect /
        # sock_read still bound connection setup and read stalls.
        self.timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=60)
        self.use_cn_mirrors = use_cn_mirrors

        # Mirror order: custom mirrors first (explicitly provided, highest
        # priority), then the built-in CN mirrors shuffled to spread load,
        # with official PyPI always last as the final fallback.
        available: list[str] = list(custom_mirrors or [])
        if use_cn_mirrors:
            cn_mirrors = self.PYPI_MIRRORS.copy()
            random.shuffle(cn_mirrors)  # Spread load across mirror sites.
            available += cn_mirrors
        if not available:
            available = [self.OFFICIAL_PYPI]
        else:
            available.append(self.OFFICIAL_PYPI)
        self._available_mirrors = available

        # Shared starting mirror; only ever advanced (never restored) so that
        # concurrent files start from a mirror after one that just failed.
        # Intentionally race-tolerant: concurrent failures may advance it more
        # than once, which is harmless since every file still walks all mirrors.
        self._preferred_mirror_idx: int = 0

        self.python_version = python_version
        self.abi = abi
        self.platform = platform
        self.all_versions = all_versions
        self.latest_patch = latest_patch

        # Progress bar fields
        self.total_files: int = 0
        self.completed_files: int = 0
        self.rich_sink: RichLogSink | None = None

        logger.info(
            f"Using timeout configuration: connect={self.timeout.connect}s, "
            f"sock_read={self.timeout.sock_read}s"
        )
        if use_cn_mirrors:
            logger.info(
                "Using Chinese mirrors (randomized) with official PyPI as fallback"
            )
        logger.info(f"Mirror order: {len(self._available_mirrors)} sites total")

        if all_versions:
            logger.info(
                "All versions mode enabled: downloading all Python 3 versions "
                "of each package"
            )
        if latest_patch:
            logger.info(
                "Latest patch mode enabled: "
                "downloading only the latest patch version for each minor version"
            )
        if dry_run:
            logger.info(f"Dry-run mode: URL list will be saved to: {self.url_list_file}")
        if python_version or abi or platform:
            filters = []
            if python_version:
                filters.append(f"python={python_version}")
            if abi:
                filters.append(f"abi={abi}")
            if platform:
                filters.append(f"platform={platform}")
            logger.info(f"Filtering wheels: {', '.join(filters)}")

    # ------------------------------------------------------------------
    # Mirror URL helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _mirror_base(mirror: str, path: str = "") -> str:
        """Join a mirror base URL and a path segment, POSIX-style.

        Works purely on the URL string (never the local filesystem), so it is
        safe on every host OS and never produces backslashes. A trailing slash
        is only kept when ``path`` itself carries one (directory-style paths
        end with "/", file paths do not).

        Args:
            mirror: Mirror base URL, e.g. ``https://mirrors.aliyun.com/pypi``.
            path: Path segment to append, e.g. ``web/packages/`` (directory)
                or ``packages/ab/cd/file.whl`` (file).

        Returns:
            The joined URL, e.g. ``https://mirrors.aliyun.com/pypi/web/packages/``.
        """
        parsed = urlparse(mirror)
        base = posixpath.normpath(parsed.path or "/")
        joined = posixpath.join(base, path) if path else base
        return urlunparse(
            (parsed.scheme, parsed.netloc, joined, parsed.params, parsed.query, parsed.fragment)
        )

    def rewrite_url(self, url: str, mirror: str | None = None) -> str:
        """Rewrite a files.pythonhosted.org URL onto the given mirror.

        Must be called with the *canonical* URL, never an already-rewritten
        one (rewriting is a no-op for URLs that do not start with the
        pythonhosted prefix, which is what makes repeated calls safe but also
        means a rewritten URL can never be re-pointed elsewhere).

        Args:
            url: The canonical download URL from the PyPI metadata API.
            mirror: Mirror base URL; defaults to the current preferred mirror.

        Returns:
            The rewritten URL, or the original URL for official PyPI and for
            URLs that are not hosted on files.pythonhosted.org.
        """
        if mirror is None:
            mirror = self._available_mirrors[self._preferred_mirror_idx]
        if mirror == self.OFFICIAL_PYPI:
            return url
        if url.startswith("https://files.pythonhosted.org/packages/"):
            # Each mirror keeps package files under its own layout prefix;
            # the hash path after "packages/" is identical everywhere.
            rest = url.removeprefix("https://files.pythonhosted.org/packages/")
            prefix = self._MIRROR_FILE_PREFIXES.get(mirror, self._MIRROR_FILE_PREFIX)
            return self._mirror_base(mirror, prefix + rest)
        return url

    # ------------------------------------------------------------------
    # Requirements parsing
    # ------------------------------------------------------------------

    @staticmethod
    def parse_package_line(line: str) -> tuple[str, str] | None:
        """Parse a requirements.txt line into (package_name, version).

        Supports ``package==version`` and ``package[extras]==version``, with an
        optional trailing environment marker (``; python_version >= '3.8'``).
        Lines starting with '#' or empty lines are ignored.

        Args:
            line: The requirement line to parse.

        Returns:
            ``(name_with_extras, version)`` if parseable, else None. The
            version is ``""`` when the line has no pin (supported for
            --all-versions mode).
        """
        line = line.split(";", 1)[0].strip()  # strip environment markers
        if not line or line.startswith("#"):
            return None

        match = re.match(r"^([\w\-\.]+)(?:\[([\w,\-]+)\])?==([\w\.\-]+)$", line)
        if match:
            base_name, extras, version = match.groups()
            full_name = f"{base_name}[{extras}]" if extras else base_name
            return full_name, version

        # Package name without version (for --all-versions mode)
        match_no_version = re.match(r"^([\w\-\.]+)(?:\[([\w,\-]+)\])?$", line)
        if match_no_version:
            base_name, extras = match_no_version.groups()
            full_name = f"{base_name}[{extras}]" if extras else base_name
            return full_name, ""

        return None

    def _parse_valid_lines(self) -> list[tuple[str, str, str]]:
        """Parse the resolved requirements into (line, name, version) triples.

        Skips blank lines, comments, and anything unparseable (with a warning),
        so downstream code only ever sees valid requirement lines.

        Returns:
            One triple per valid requirement line, in input order.
        """
        valid: list[tuple[str, str, str]] = []
        for line in self.requirements_content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parsed = self.parse_package_line(line)
            if parsed:
                valid.append((line, parsed[0], parsed[1]))
            else:
                logger.warning(f"Skipping unparseable line: {stripped}")
        return valid

    # ------------------------------------------------------------------
    # Wheel tag matching
    # ------------------------------------------------------------------

    @staticmethod
    def parse_wheel_filename(filename: str) -> dict[str, str | None] | None:
        """Parse a wheel filename according to PEP 425.

        Format: ``{distribution}-{version}(-{build})?-{python}-{abi}-{platform}.whl``

        Args:
            filename: The wheel filename to parse.

        Returns:
            Dict with keys ``name``, ``version``, ``build``, ``python``,
            ``abi``, ``platform`` (build is None when absent), or None if the
            filename is not a parseable wheel.
        """
        if not filename.endswith(".whl"):
            return None

        name_parts = filename[:-4].split("-")
        if len(name_parts) < 5:
            return None

        if len(name_parts) >= 6:  # optional build tag present
            name, version, build, python, abi, platform_tag = name_parts[:6]
        else:
            name, version, python, abi, platform_tag = name_parts[:5]
            build = None
        return {
            "name": name,
            "version": version,
            "build": build,
            "python": python,
            "abi": abi,
            "platform": platform_tag,
        }

    @staticmethod
    def _is_py2_only(python_tags: list[str]) -> bool:
        """Check whether a wheel's Python tags indicate a Python 2 only build.

        A wheel is Python 2 only when it has no py3/cp3 tag and at least one
        py2/cp2 tag (e.g. ``py2``, ``py27``, ``cp27``). Compressed tags like
        ``py2.py3`` split into both families and therefore never match.

        Args:
            python_tags: The wheel's Python tags, already split on '.'.

        Returns:
            True if the wheel cannot be used on any Python 3 interpreter.
        """
        if not python_tags:
            return False
        if any(tag.startswith("py3") or tag.startswith("cp3") for tag in python_tags):
            return False
        return any(tag.startswith("py2") or tag.startswith("cp2") for tag in python_tags)

    def matches_filter(
        self,
        filename: str,
        python_version: str | None = None,
        abi: str | None = None,
        platform: str | None = None,
    ) -> bool:
        """Check whether a file matches the specified filters.

        Source distributions (non-wheel files) always pass. Python 2 only
        wheels (py2*/cp2* tags without any py3/cp3 tag) are always rejected.

        Args:
            filename: The filename to check.
            python_version: Python version filter (e.g., "cp311", "py2.py3").
            abi: ABI filter (e.g., "cp311", "abi3", "none").
            platform: Platform filter (e.g., "manylinux_2_17_x86_64", "any").

        Returns:
            True if the file passes all specified filters.
        """
        wheel_info = self.parse_wheel_filename(filename)
        if not wheel_info:
            return True  # non-wheel files (sdists) always pass

        file_python_tags = (wheel_info["python"] or "").split(".")
        if self._is_py2_only(file_python_tags):
            logger.debug(f"Skipping Python 2 only wheel: {filename}")
            return False

        # Each filter uses compressed-tag matching: any tag of the filter
        # (split on '.') must appear in the file's tag list.
        if python_version:
            filter_python_tags = python_version.split(".")
            if not any(tag in file_python_tags for tag in filter_python_tags):
                return False

        if abi:
            file_abi_tags = (wheel_info["abi"] or "").split(".")
            if not any(tag in file_abi_tags for tag in abi.split(".")):
                return False

        if platform:
            file_platform_tags = (wheel_info["platform"] or "").split(".")
            if not any(tag in file_platform_tags for tag in platform.split(".")):
                return False

        return True

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    async def fetch_metadata(self, package_with_extras: str) -> dict[str, Any] | None:
        """Fetch package metadata mirror-first, with the official API as fallback.

        Several CN mirrors proxy the PyPI JSON API under ``{mirror}/web/json/``;
        each mirror gets one attempt and failures (404/5xx/network/garbage)
        move on to the next one. The official PyPI JSON API sits at the end of
        the mirror list as the final authority: a 404 from it cleanly
        identifies "package does not exist" (as opposed to "this mirror cannot
        serve metadata"), and it saves the run when every mirror is
        unreachable. Results are cached for the whole run, so each package is
        fetched exactly once even though phase 1 (counting) and phase 2
        (downloading) both need it.

        Args:
            package_with_extras: Package name, potentially with extras
                (extras are stripped before building the URL).

        Returns:
            The metadata dict, or None if the package does not exist or no
            source could serve it.
        """
        package_name = re.sub(r"\[.*?\]", "", package_with_extras)
        if package_name in self._metadata_cache:
            return self._metadata_cache[package_name]

        async with self._metadata_semaphore:
            metadata = await self._fetch_metadata_cycle(package_name)
        self._metadata_cache[package_name] = metadata
        return metadata

    async def _fetch_metadata_cycle(self, package_name: str) -> dict[str, Any] | None:
        """Try every mirror once (preferred first, official last) for metadata.

        The JSON endpoint path follows each mirror's file layout
        (``web/json/`` or ``json/``, derived from ``_MIRROR_FILE_PREFIXES``).
        The preferred-mirror pointer that anchors file downloads is
        deliberately not advanced here: it only ever moves when downloads
        fail, so metadata availability does not reorder the download order.

        Args:
            package_name: Package name without extras.

        Returns:
            The metadata dict from the first source that served valid JSON,
            or None when the package is confirmed missing or all sources failed.
        """
        session = self.session
        if session is None:
            raise RuntimeError("ClientSession is not initialized; call run() first")

        total_mirrors = len(self._available_mirrors)
        start_idx = self._preferred_mirror_idx
        official_404 = False

        for offset in range(total_mirrors):
            idx = (start_idx + offset) % total_mirrors
            mirror = self._available_mirrors[idx]
            if mirror == self.OFFICIAL_PYPI:
                url = f"{self.PYPI_JSON_API}/{package_name}/json"
            else:
                # The JSON endpoint follows each mirror's file layout: TUNA-style
                # mirrors serve it under "web/json/", plain-layout mirrors
                # (tencent, volces) under "json/". Mirrors without the endpoint
                # (huawei) simply fail here and the cycle moves on.
                file_prefix = self._MIRROR_FILE_PREFIXES.get(
                    mirror, self._MIRROR_FILE_PREFIX
                )
                json_prefix = file_prefix.removesuffix("packages/") + "json/"
                url = self._mirror_base(mirror, f"{json_prefix}{package_name}")

            try:
                async with session.get(url, timeout=self.timeout) as resp:
                    if resp.status == 404 and mirror == self.OFFICIAL_PYPI:
                        # Authoritative answer: the package does not exist.
                        official_404 = True
                        break
                    if resp.status != 200:
                        logger.debug(
                            f"Metadata unavailable from {mirror} (HTTP {resp.status})"
                        )
                        continue
                    metadata = json.loads((await resp.read()).decode("utf-8"))
            except (aiohttp.ClientError, TimeoutError) as e:
                logger.debug(f"Metadata fetch failed on {mirror}: {e}")
                continue
            except json.JSONDecodeError as e:
                logger.debug(f"Invalid metadata JSON from {mirror}: {e}")
                continue

            # Note: the preferred-mirror pointer is deliberately NOT moved
            # here — it only ever moves on download failures (see
            # download_file), so metadata availability does not reorder the
            # download order.
            logger.debug(f"Metadata for {package_name} served by {mirror}")
            return metadata

        if official_404:
            logger.error(f"Package not found on PyPI: {package_name}")
        else:
            logger.error(f"All mirrors failed to serve metadata for {package_name}")
        return None

    def find_version_info(
        self, metadata: dict[str, Any], version: str
    ) -> list[dict[str, Any]] | None:
        """Find release information for a specific package version.

        Args:
            metadata: The full package metadata dict.
            version: The exact version string to look up.

        Returns:
            The list of release file dicts, or None if the version is unknown.
        """
        return metadata.get("releases", {}).get(version)

    def find_all_python3_versions(
        self, metadata: dict[str, Any]
    ) -> dict[str, list[dict[str, Any]]]:
        """Find all Python 3 compatible versions from package metadata.

        A version counts as compatible if it has any source distribution or
        any wheel with a py3/cp3 Python tag.

        Args:
            metadata: The full package metadata dict.

        Returns:
            Dict mapping version strings to their release file lists; only
            includes versions that have Python 3 compatible files.
        """
        all_releases = metadata.get("releases", {})
        python3_releases: dict[str, list[dict[str, Any]]] = {}

        for version, files in all_releases.items():
            if not files:
                continue

            has_py3_files = False
            for file_info in files:
                filename = file_info.get("filename", "")
                # Source distributions are always compatible
                if not filename.endswith(".whl"):
                    has_py3_files = True
                    break
                wheel_info = self.parse_wheel_filename(filename)
                if wheel_info:
                    python_tags = (wheel_info["python"] or "").split(".")
                    if any(
                        tag.startswith("py3") or tag.startswith("cp3")
                        for tag in python_tags
                    ):
                        has_py3_files = True
                        break

            if has_py3_files:
                python3_releases[version] = files

        return python3_releases

    def filter_latest_patch_versions(
        self, versions_dict: dict[str, list[dict[str, Any]]]
    ) -> dict[str, list[dict[str, Any]]]:
        """Filter versions to keep only the latest patch for each minor version.

        For example: 2.1.3, 2.1.5, 2.1.9 -> keep only 2.1.9. Unparseable
        version strings are dropped from the result (with a warning).

        Args:
            versions_dict: Dict mapping version strings to release file lists.

        Returns:
            Filtered dict containing only the latest patch per minor version.
        """
        # Group versions by (major, minor); pre-releases group by their base.
        versions_by_minor: dict[tuple[int, int], list[tuple[Version, str]]] = {}

        for version_str in versions_dict.keys():
            try:
                version_obj = Version(version_str)
                key = (version_obj.major, version_obj.minor)
                versions_by_minor.setdefault(key, []).append((version_obj, version_str))
            except InvalidVersion:
                logger.warning(
                    f"Could not parse version '{version_str}', keeping it anyway"
                )
                continue

        filtered_versions: dict[str, list[dict[str, Any]]] = {}
        for minor_key, version_list in versions_by_minor.items():
            # PEP 440 ordering; reverse so the highest version comes first.
            version_list.sort(key=lambda x: x[0], reverse=True)
            latest_version_str = version_list[0][1]
            filtered_versions[latest_version_str] = versions_dict[latest_version_str]

            all_versions_in_group = [v[1] for v in version_list]
            if len(all_versions_in_group) > 1:
                logger.debug(
                    f"Minor version {minor_key[0]}.{minor_key[1]}.x: "
                    f"keeping {latest_version_str} out of {len(all_versions_in_group)} versions"
                )

        logger.info(
            f"Filtered from {len(versions_dict)} to {len(filtered_versions)} versions "
            f"(kept latest patch for each minor version)"
        )
        return filtered_versions

    def _count_downloadable_files(self, metadata: dict[str, Any], version: str) -> int:
        """Count the files that would be downloaded for a package.

        Applies the same version selection and wheel filters as the download
        phase, so the progress bar total matches the number of files actually
        attempted.

        Args:
            metadata: The full package metadata dict.
            version: The pinned version (ignored in --all-versions /
                --latest-patch modes).

        Returns:
            The number of files that match all filters.
        """
        count = 0
        skipped_py2 = 0

        if self.all_versions or self.latest_patch:
            versions_to_count = self.find_all_python3_versions(metadata)
            if self.latest_patch:
                versions_to_count = self.filter_latest_patch_versions(versions_to_count)
        else:
            version_info = self.find_version_info(metadata, version)
            if not version_info:
                return 0
            versions_to_count = {version: version_info}

        for _ver, version_files in versions_to_count.items():
            for file_info in version_files:
                filename = file_info.get("filename", "")
                if self.matches_filter(
                    filename, self.python_version, self.abi, self.platform
                ):
                    count += 1
                else:
                    wheel_info = self.parse_wheel_filename(filename)
                    if wheel_info and self._is_py2_only(
                        (wheel_info["python"] or "").split(".")
                    ):
                        skipped_py2 += 1

        if skipped_py2 > 0:
            logger.debug(f"Skipped {skipped_py2} Python 2 only files in count")

        return count

    # ------------------------------------------------------------------
    # Hashing
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_value(expected_hash: str | None) -> str | None:
        """Extract the hex digest from a "sha256=..." style hash spec.

        Args:
            expected_hash: Hash spec such as ``sha256=abc...`` or a bare hex
                digest, or None.

        Returns:
            The bare hex digest, or None when no hash was given.
        """
        if not expected_hash:
            return None
        prefix = "sha256="
        if expected_hash.startswith(prefix):
            return expected_hash[len(prefix) :]
        return expected_hash

    @staticmethod
    def compute_hash(file_path: Path, algo: str = "sha256") -> str:
        """Compute the cryptographic hash of a file.

        Reads the file in 8 KB chunks so arbitrarily large files are hashed
        without loading them into memory.

        Args:
            file_path: Path to the file to hash.
            algo: Hash algorithm name accepted by ``hashlib.new``.

        Returns:
            The hexadecimal digest.
        """
        h = hashlib.new(algo)
        with file_path.open("rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    async def compute_hash_async(file_path: Path, algo: str = "sha256") -> str:
        """Compute the cryptographic hash of a file via the thread pool.

        Hashing is CPU-bound, so it must run off the event loop to avoid
        stalling concurrent downloads.

        Args:
            file_path: Path to the file to hash.
            algo: Hash algorithm name accepted by ``hashlib.new``.

        Returns:
            The hexadecimal digest.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, PackageDownloader.compute_hash, file_path, algo
        )

    # ------------------------------------------------------------------
    # Progress tracking
    # ------------------------------------------------------------------

    def _init_progress_bar(self, total: int) -> None:
        """Initialize progress tracking with the total file count.

        Progress is best-effort: any Rich failure degrades to log-only mode.

        Args:
            total: Total number of files that will be processed.
        """
        try:
            self.total_files = total
            self.completed_files = 0
            if self.rich_sink:
                self.rich_sink.init_progress(total)
            logger.info(f"Initialized progress tracking with {total} total files")
        except Exception as e:  # noqa: BLE001 - progress is best-effort
            logger.warning(
                f"Failed to initialize progress tracking: {e}. Continuing with log-only mode."
            )

    def _update_progress(self, n: int = 1) -> None:
        """Advance progress by n files (best-effort, never raises).

        Args:
            n: Number of completed files to add.
        """
        try:
            self.completed_files += n
            if self.rich_sink:
                self.rich_sink.update_progress(n)
        except Exception as e:  # noqa: BLE001 - progress is best-effort
            logger.debug(f"Progress update failed: {e}")

    # ------------------------------------------------------------------
    # Downloading
    # ------------------------------------------------------------------

    async def _remove_quietly(self, path: Path) -> None:
        """Delete a file if it exists, ignoring errors (best-effort cleanup).

        Args:
            path: File to remove.
        """
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: path.unlink(missing_ok=True))
        except Exception as e:  # noqa: BLE001 - cleanup is best-effort
            logger.debug(f"Could not remove partial file {path}: {e}")

    async def _download_once(
        self,
        url: str,
        dest_path: Path,
        tmp_path: Path,
        expected_hash: str | None,
    ) -> bool:
        """Stream one download to a .part file, verify the hash, rename atomically.

        The response body is consumed in 1 MiB chunks: the SHA-256 digest is
        computed in the event loop (cheap per chunk) while every write goes
        through the thread pool so disk latency never blocks other downloads.
        Nothing is written to the final filename until the hash check passes.

        Args:
            url: The (already mirror-rewritten) URL to fetch.
            dest_path: Final destination path.
            tmp_path: Temporary ``.part`` path written first.
            expected_hash: Expected SHA-256 spec ("sha256=..." or bare hex),
                or None to skip verification.

        Returns:
            True when the file was downloaded and (if a hash was given) verified.

        Raises:
            aiohttp.ClientError: On HTTP/network errors.
            TimeoutError: On read stalls.
            _HashMismatchError: When the digest does not match.
            OSError: On local filesystem errors.
        """
        if self.session is None:
            raise RuntimeError("ClientSession is not initialized; call run() first")

        loop = asyncio.get_running_loop()
        hasher = hashlib.sha256()
        try:
            async with self.session.get(url, timeout=self.timeout) as resp:
                resp.raise_for_status()
                fh = await loop.run_in_executor(None, tmp_path.open, "wb")
                try:
                    async for chunk in resp.content.iter_chunked(self.DOWNLOAD_CHUNK_SIZE):
                        hasher.update(chunk)
                        await loop.run_in_executor(None, fh.write, chunk)
                finally:
                    await loop.run_in_executor(None, fh.close)

            expected_value = self._hash_value(expected_hash)
            if expected_value is not None and hasher.hexdigest() != expected_value:
                raise _HashMismatchError(
                    f"expected {expected_value}, got {hasher.hexdigest()}"
                )

            # Atomic publish: the final name only ever contains complete data.
            await loop.run_in_executor(None, os.replace, tmp_path, dest_path)
            logger.info(f"Downloaded: {dest_path.name}")
            self._update_progress(1)
            return True
        except BaseException:
            # Never leave a truncated .part behind, whatever went wrong.
            await self._remove_quietly(tmp_path)
            raise

    async def download_file(
        self, url: str, filename: str, expected_hash: str | None = None
    ) -> bool:
        """Download one file with retry, mirror switching, and hash verification.

        ``url`` must be the canonical files.pythonhosted.org URL from the PyPI
        metadata API. It is rewritten to the active mirror on every attempt,
        so switching mirrors always changes the URL actually fetched.

        Concurrency: the per-file semaphore is acquired only for the retry
        loop, so cache hits never occupy a download slot.

        Args:
            url: The original download URL.
            filename: The target filename.
            expected_hash: Expected SHA-256 hash (format: "sha256=..." or bare hex).

        Returns:
            True if the download succeeded or the file already exists with a
            matching hash; False after all attempts are exhausted.
        """
        if self.session is None:
            raise RuntimeError("ClientSession is not initialized; call run() first")

        dest_path = self.download_dir / filename
        tmp_path = dest_path.with_name(dest_path.name + ".part")
        loop = asyncio.get_running_loop()

        # Skip existing files that already have the right content.
        if await loop.run_in_executor(None, dest_path.exists):
            expected_value = self._hash_value(expected_hash)
            if expected_value is None:
                # No hash available: trust the existing file (resumable runs).
                logger.debug(f"File already exists (no hash to verify), skipping: {filename}")
                self._update_progress(1)
                return True
            existing_hash = await self.compute_hash_async(dest_path)
            if existing_hash == expected_value:
                logger.debug(f"File exists with valid hash, skipping: {filename}")
                self._update_progress(1)
                return True
            logger.warning(f"File exists but hash mismatch, re-downloading: {filename}")

        total_mirrors = len(self._available_mirrors)
        start_idx = self._preferred_mirror_idx
        mirror_offset = 0
        attempts_on_mirror = 0

        async with self.semaphore:
            for attempt in range(1, self.DEFAULT_RETRIES + 1):
                mirror = self._available_mirrors[(start_idx + mirror_offset) % total_mirrors]
                rewritten_url = self.rewrite_url(url, mirror)
                # TRACE goes to the file log only (console sinks are DEBUG+).
                logger.trace(f"Downloading: {rewritten_url}")

                try:
                    return await self._download_once(
                        rewritten_url, dest_path, tmp_path, expected_hash
                    )
                except _HashMismatchError as e:
                    # A corrupt/truncated payload is a mirror problem: retry
                    # the same file from the next mirror.
                    warning = f"Hash mismatch for {filename} from {mirror}: {e}"
                except aiohttp.ClientError as e:
                    warning = f"Client error downloading {rewritten_url}: {e}"
                except TimeoutError:
                    warning = (
                        f"Timeout downloading {rewritten_url} "
                        f"(no data received for {self.timeout.sock_read}s)"
                    )
                except OSError as exc:
                    if exc.errno in _FATAL_LOCAL_ERRNO:
                        logger.critical(
                            f"Fatal local I/O error writing {filename} "
                            f"(errno {exc.errno}): {exc}. Fix the local filesystem issue "
                            f"and re-run; already-downloaded files are skipped automatically."
                        )
                        raise DiskFullError(str(exc), errno_code=exc.errno) from exc
                    logger.error(f"Local I/O error downloading {filename}: {exc}")
                    return False
                except Exception as e:  # noqa: BLE001 - keep the worker alive
                    warning = f"Unexpected error downloading {rewritten_url}: {e}"

                logger.warning(f"Attempt {attempt}/{self.DEFAULT_RETRIES}: {warning}")
                attempts_on_mirror += 1
                if attempts_on_mirror >= self.RETRIES_PER_MIRROR:
                    attempts_on_mirror = 0
                    mirror_offset += 1
                    # Advance the shared pointer (never backwards) so later files
                    # start from a mirror after the one that just failed.
                    self._preferred_mirror_idx = (
                        self._preferred_mirror_idx + 1
                    ) % total_mirrors
                    logger.info(
                        f"Switching mirror for {filename}: {mirror} -> "
                        f"{self._available_mirrors[(start_idx + mirror_offset) % total_mirrors]}"
                    )

        logger.error(
            f"Failed to download after {self.DEFAULT_RETRIES} attempts: {filename}"
        )
        self._update_progress(1)  # Count failed downloads toward progress too
        return False

    # ------------------------------------------------------------------
    # Package processing
    # ------------------------------------------------------------------

    async def process_package(self, line: str) -> dict[str, Any]:
        """Process a single package definition from the resolved requirements.

        Selects the versions to download (pinned / all / latest-patch),
        filters their files, and downloads everything concurrently. Metadata
        comes from the cache populated in phase 1. Never raises for ordinary
        failures — problems are reported in the returned status dict.

        Args:
            line: The requirement line (must be parseable by
                :meth:`parse_package_line`).

        Returns:
            Status dict with keys ``package``, ``version``, ``status`` and
            ``details``. Status is one of "Synchronized", "Partial Sync",
            "No Files", "Failed", or "Error (Pre-filter)".
        """
        parsed = self.parse_package_line(line)
        if not parsed:
            # Lines are pre-filtered in run(); kept for robustness.
            return {
                "package": line.strip() if line.strip() else "N/A",
                "version": "N/A",
                "status": "Error (Pre-filter)",
                "details": "Unexpected unparsable line",
            }
        name, version = parsed
        package_status: dict[str, Any] = {
            "package": name,
            "version": version,
            "status": "Failed",
            "details": "",
        }

        try:
            metadata = await self.fetch_metadata(name)  # cache hit from phase 1
            if not metadata:
                package_status["details"] = "Failed to fetch metadata"
                return package_status

            versions_to_download: dict[str, list[dict[str, Any]]]
            if self.all_versions or self.latest_patch:
                versions_to_download = self.find_all_python3_versions(metadata)
                if not versions_to_download:
                    package_status["details"] = "No Python 3 compatible versions found"
                    return package_status
                if self.latest_patch:
                    versions_to_download = self.filter_latest_patch_versions(
                        versions_to_download
                    )
                    package_status["version"] = (
                        f"latest-patch ({len(versions_to_download)} versions)"
                    )
                else:
                    package_status["version"] = f"all ({len(versions_to_download)} versions)"
            else:
                version_info = self.find_version_info(metadata, version)
                if not version_info:
                    package_status["details"] = "No release info found"
                    return package_status
                versions_to_download = {version: version_info}

            # Collect matching files first so the summary reflects what was
            # attempted (not just what succeeded).
            files_to_download: list[tuple[str, str, str | None]] = []
            for version_files in versions_to_download.values():
                for file_info in version_files:
                    filename = file_info.get("filename", "")
                    url = file_info.get("url", "")
                    if not filename or not url:
                        continue
                    if not self.matches_filter(
                        filename, self.python_version, self.abi, self.platform
                    ):
                        logger.debug(f"Skipping {filename} (doesn't match filters)")
                        continue
                    expected_hash: str | None = None
                    digests = file_info.get("digests") or {}
                    if "sha256" in digests:
                        expected_hash = f"sha256={digests['sha256']}"
                    files_to_download.append((url, filename, expected_hash))

            if not files_to_download:
                package_status["status"] = "No Files"
                package_status["details"] = "No downloadable files found for this version"
                return package_status

            if self.dry_run:
                for url, _filename, _hash in files_to_download:
                    # Keep the canonical URL: deterministic across runs and
                    # directly usable with wget/aria2c.
                    self.download_urls.append(url)
                    logger.info(f"[Dry-run] Would download: {url}")
                    self._update_progress(1)
                package_status["status"] = "Synchronized"
                package_status["details"] = (
                    f"All {len(files_to_download)} file(s) processed (dry-run)"
                )
                return package_status

            download_results = await asyncio.gather(
                *(
                    self.download_file(url, filename, expected_hash)
                    for url, filename, expected_hash in files_to_download
                )
            )
            success_count = sum(1 for ok in download_results if ok)

            if success_count == len(files_to_download):
                package_status["status"] = "Synchronized"
                package_status["details"] = f"All {len(files_to_download)} file(s) processed"
            elif success_count > 0:
                package_status["status"] = "Partial Sync"
                package_status["details"] = (
                    f"{success_count}/{len(files_to_download)} file(s) processed"
                )
            else:
                package_status["details"] = "No files downloaded"
            return package_status
        except LocalIOFatalError:
            # Re-raise immediately: local filesystem errors cannot be fixed by
            # retrying with a different mirror.
            raise
        except Exception as e:  # noqa: BLE001 - report per-package failures
            logger.exception(
                f"An unhandled error occurred while processing package line "
                f"'{line.strip()}': {e}"
            )
            package_status["details"] = f"Unhandled error: {e}"
            return package_status

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    def _cleanup_stale_part_files(self) -> int:
        """Delete leftover ``.part`` temp files from crashed previous runs.

        A hard kill (power loss, ``kill -9``) can leave ``.part`` files in the
        download directory. They are harmless (the next download of the same
        file overwrites them), but with large ``--all-versions`` sets they can
        accumulate. Returns the number of files removed.
        """
        stale = sorted(self.download_dir.glob("*.part"))
        for path in stale:
            try:
                path.unlink()
            except OSError as e:
                logger.debug(f"Could not remove stale partial file {path}: {e}")
        return len(stale)

    def _write_url_list(self) -> None:
        """Write the collected canonical URLs to the URL list file (dry-run only)."""
        if self.download_urls:
            self.url_list_file.write_text(
                "\n".join(self.download_urls) + "\n", encoding="utf-8"
            )
            logger.info(f"URL list saved to {self.url_list_file}")
        else:
            logger.info("No download URLs were collected; URL list file will not be created.")

    async def run(self) -> list[dict[str, Any]]:
        """Process all packages: fetch metadata concurrently, then download all files.

        Creates the download directory, installs a thread pool for blocking
        file I/O (file I/O releases the GIL, so threads run in parallel), and
        runs both phases inside a single aiohttp session. The executor is
        always shut down, even when the run raises.

        Returns:
            One status dict per processed package (see :meth:`process_package`).

        Raises:
            LocalIOFatalError: When a fatal local filesystem error (disk full,
                read-only filesystem, ...) aborts the run.
        """
        logger.info("Starting package download process")
        self.download_dir.mkdir(parents=True, exist_ok=True)

        # Mimic pip's User-Agent so mirrors do not block non-browser traffic.
        python_version = ".".join(map(str, sys.version_info[:3]))
        headers = {"User-Agent": f"pip/24.0 (python {python_version})"}

        max_workers = min(32, (os.cpu_count() or 1) * 4)
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(max_workers=max_workers)
        loop.set_default_executor(executor)
        logger.info(f"Using {max_workers} threads for I/O operations")

        # Remove .part leftovers from crashed runs before any downloads start.
        stale_parts = await loop.run_in_executor(None, self._cleanup_stale_part_files)
        if stale_parts:
            logger.info(f"Removed {stale_parts} stale .part file(s) from previous runs")

        try:
            async with aiohttp.ClientSession(headers=headers) as self.session:
                package_lines = self._parse_valid_lines()

                # Phase 1: fetch all metadata concurrently (mirror-first, cached).
                logger.info(
                    f"Phase 1: Fetching metadata for {len(package_lines)} packages..."
                )
                metadata_list = await asyncio.gather(
                    *(self.fetch_metadata(name) for _line, name, _version in package_lines)
                )

                # Count files from the same metadata that phase 2 will use, so
                # the progress bar total is exact.
                total_files = 0
                for (_line, name, version), metadata in zip(
                    package_lines, metadata_list, strict=True
                ):
                    file_count = (
                        self._count_downloadable_files(metadata, version)
                        if metadata
                        else 0
                    )
                    total_files += file_count
                    logger.debug(f"{name}: {file_count} files to download")
                logger.info(f"Total files to download: {total_files}")
                self._init_progress_bar(total_files)

                # Phase 2: download all files concurrently (metadata served
                # from cache, so no package metadata is fetched twice).
                logger.info("Phase 2: Downloading files...")
                results = await asyncio.gather(
                    *(self.process_package(line) for line, _name, _version in package_lines)
                )

            if self.dry_run:
                self._write_url_list()
            return results
        finally:
            executor.shutdown(wait=True)
