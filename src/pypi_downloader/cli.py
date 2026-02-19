"""
pypi_downloader.py - A Python script to download packages from PyPI mirrors
with automatic fallback mechanism when mirrors fail.
"""

import argparse
import asyncio
import hashlib
import json
import random
import re
import subprocess
import sys
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import aiohttp
from loguru import logger
from packaging.version import InvalidVersion, Version
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table
from rich.text import Text


class RichLogSink:
    """使用 Rich Live 显示最后 N 行日志和进度条"""

    def __init__(self, max_lines=25):
        """
        Args:
            max_lines: 最多显示的日志行数（不包括进度条）
        """
        self.max_lines = max_lines
        self.lines = deque(maxlen=max_lines)
        self.console = Console(file=sys.stderr)
        self.live = None
        self.progress = Progress(
            TextColumn("[cyan]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("{task.completed}/{task.total} files"),
        )
        self.task_id = None

    def start(self):
        """启动 Live 显示"""
        if not self.live:
            # vertical_overflow="visible" 让内容从底部开始
            self.live = Live(
                self._render(),
                console=self.console,
                refresh_per_second=10,
                vertical_overflow="visible",
            )
            self.live.start()

    def write(self, message):
        """写入日志消息"""
        msg = message.rstrip()

        # Truncate long messages to 120 characters
        if len(msg) > 120:
            # Check if message contains a URL (http:// or https://)
            if "http://" in msg or "https://" in msg:
                # For URLs, extract and show: prefix + mirror domain + "..." + package filename
                # This shows which mirror is being used and what package
                try:
                    # Find the URL in the message
                    url_start = msg.find("http://")
                    if url_start == -1:
                        url_start = msg.find("https://")

                    if url_start != -1:
                        prefix = msg[:url_start]  # e.g., "Downloading: "
                        url_part = msg[url_start:]

                        # Extract domain (e.g., "mirrors.aliyun.com")
                        # Format: http://domain/path/to/file
                        protocol_end = url_part.find("://") + 3
                        path_start = url_part.find("/", protocol_end)

                        if path_start != -1:
                            domain = url_part[
                                :path_start
                            ]  # e.g., "http://mirrors.aliyun.com"
                            path = url_part[
                                path_start:
                            ]  # e.g., "/pypi/web/packages/.../file.whl"

                            # Extract package filename from path (last component)
                            filename = path.split("/")[-1]

                            # Build truncated message: prefix + domain + "..." + filename
                            # Format: "Downloading: http://mirrors.aliyun.com...black-24.1.1.whl"
                            truncated = f"{prefix}{domain}...{filename}"

                            # If still too long, truncate filename
                            if len(truncated) > 120:
                                base_len = len(f"{prefix}{domain}...")
                                available = 120 - base_len - 3  # Reserve 3 for "..."
                                if available > 10:
                                    filename = filename[:available] + "..."
                                    truncated = f"{prefix}{domain}...{filename}"
                                else:
                                    # Fallback: just show domain
                                    truncated = f"{prefix}{domain}..."

                            msg = truncated
                        else:
                            # Fallback: show beginning
                            msg = msg[:117] + "..."
                    else:
                        # No URL found, show beginning
                        msg = msg[:117] + "..."
                except Exception:
                    # If parsing fails, fallback to simple truncation
                    msg = msg[:117] + "..."
            else:
                # For non-URL messages, show the beginning
                msg = msg[:117] + "..."

        self.lines.append(msg)
        self._update_display()

    def init_progress(self, total: int):
        """初始化进度条"""
        self.task_id = self.progress.add_task("Downloading", total=total)
        self._update_display()

    def update_progress(self, advance: int = 1):
        """更新进度"""
        if self.task_id is not None:
            self.progress.update(self.task_id, advance=advance)
            self._update_display()

    def _render(self):
        """渲染显示内容"""
        # 日志文本
        log_lines = list(self.lines)
        log_text = "\n".join(log_lines) if log_lines else ""

        # 如果有进度条，使用 Group 组合日志和进度条
        if self.task_id is not None:
            separator = "─" * 80
            return Group(Text(log_text), Text(separator, style="dim"), self.progress)
        return Text(log_text)

    def _update_display(self):
        """更新显示内容"""
        if self.live:
            self.live.update(self._render())

    def flush(self):
        pass

    def stop(self):
        """停止 Live 显示"""
        if self.live:
            self.live.stop()
            self.live = None


class PackageDownloader:
    """
    A class designed to download Python packages from multiple PyPI mirrors with fallback.

    Supports parsing requirements files, fetching package metadata from multiple mirrors,
    rewriting download URLs, and downloading files asynchronously with concurrency control.
    Provides detailed status reporting and automatic mirror switching on failure.
    """

    PYPI_MIRRORS = [
        "http://mirrors.aliyun.com/pypi",
        "https://mirrors.cloud.tencent.com/pypi",
        "https://mirror.nju.edu.cn/pypi",
        "https://mirror.nyist.edu.cn/pypi",
        "https://mirror.sjtu.edu.cn/pypi",
        "https://mirrors.bfsu.edu.cn/pypi",
        "https://mirrors.jlu.edu.cn/pypi",
        "https://mirrors.neusoft.edu.cn/pypi",
        "https://mirrors.njtech.edu.cn/pypi",
        "https://mirrors.pku.edu.cn/pypi",
        "https://mirrors.qlu.edu.cn/pypi",
        "https://mirrors.tuna.tsinghua.edu.cn/pypi",
        "https://mirrors.ustc.edu.cn/pypi",
        "https://mirrors.zju.edu.cn/pypi",
    ]

    OFFICIAL_PYPI = "https://pypi.org"

    DEFAULT_CONCURRENCY: int = 16  # Reduced for better stability with mirrors
    DEFAULT_RETRIES: int = (
        32  # Total retries across all mirrors (15 sites * 2 retries + buffer)
    )
    RETRIES_PER_MIRROR: int = 2  # Retries per mirror before switching

    def __init__(
        self,
        requirements_content: str,
        dry_run: bool = False,
        concurrency: int = DEFAULT_CONCURRENCY,
        download_dir: Path = Path.cwd() / "pypi",
        use_cn_mirrors: bool = False,
        python_version: Optional[str] = None,
        abi: Optional[str] = None,
        platform: Optional[str] = None,
        all_versions: bool = False,
        latest_patch: bool = False,
        url_list_path: Optional[Path] = None,
    ) -> None:
        """
        Initialize the PackageDownloader.

        Args:
            requirements_content: Content of requirements (resolved dependencies as string).
            dry_run: If True, only generates URL list without downloading.
            concurrency: Maximum number of concurrent downloads.
            download_dir: Directory to save downloaded packages.
            use_cn_mirrors: If True, use Chinese mirrors with fallback. Otherwise use official PyPI.
            python_version: Python version filter (e.g., "cp311", "py3").
            abi: ABI filter (e.g., "cp311", "abi3", "none").
            platform: Platform filter (e.g., "manylinux_2_17_x86_64", "win_amd64", "any").
            all_versions: If True, download all available versions of each package (Python 3 only).
            latest_patch: If True, download only the latest patch version for each minor version.
            url_list_path: Path to save URL list. Defaults to ./url_list.txt in current directory.
        """
        self.requirements_content: str = requirements_content
        self.session: Optional[aiohttp.ClientSession] = None
        self.dry_run: bool = dry_run
        self.semaphore: asyncio.Semaphore = asyncio.Semaphore(concurrency)
        self.download_urls: List[str] = []
        self.download_dir = download_dir
        self.url_list_file = (
            url_list_path if url_list_path else Path.cwd() / "url_list.txt"
        )
        self.timeout: aiohttp.ClientTimeout = aiohttp.ClientTimeout(
            total=None, connect=60, sock_read=60
        )
        self.use_cn_mirrors = use_cn_mirrors

        # Initialize mirror list: randomize CN mirrors, then add official PyPI at the end
        if use_cn_mirrors:
            self._available_mirrors = self.PYPI_MIRRORS.copy()
            random.shuffle(self._available_mirrors)  # Randomize CN mirrors
            self._available_mirrors.append(
                self.OFFICIAL_PYPI
            )  # Official PyPI as last resort
        else:
            self._available_mirrors = [self.OFFICIAL_PYPI]

        self._current_mirror_idx: int = 0

        self.python_version = python_version
        self.abi = abi
        self.platform = platform
        self.all_versions = all_versions
        self.latest_patch = latest_patch
        self.all_versions = all_versions

        # Progress bar fields
        self.total_files: int = 0
        self.completed_files: int = 0
        self.rich_sink: Optional[RichLogSink] = None

        logger.info(
            f"Using timeout configuration: connect={self.timeout.connect}s, "
            f"sock_read={self.timeout.sock_read}s"
        )
        if use_cn_mirrors:
            logger.info(
                f"Using Chinese mirrors (randomized) with official PyPI as fallback"
            )
            logger.info(f"Mirror order: {len(self._available_mirrors)} sites total")
        else:
            logger.info(f"Using official PyPI (https://pypi.org)")

        if all_versions:
            logger.info(
                "All versions mode enabled: downloading all Python 3 versions of each package"
            )

        if latest_patch:
            logger.info(
                "Latest patch mode enabled: downloading only the latest patch version for each minor version"
            )

        if dry_run:
            logger.info(
                f"Dry-run mode: URL list will be saved to: {self.url_list_file}"
            )

        if python_version or abi or platform:
            filters = []
            if python_version:
                filters.append(f"python={python_version}")
            if abi:
                filters.append(f"abi={abi}")
            if platform:
                filters.append(f"platform={platform}")
            logger.info(f"Filtering wheels: {', '.join(filters)}")

    async def get_next_mirror(self) -> str:
        """
        Get the next available mirror in the list, cycling back to the first when needed.

        Returns:
            The URL of the next mirror to try.
        """
        self._current_mirror_idx = (self._current_mirror_idx + 1) % len(
            self._available_mirrors
        )
        return self._available_mirrors[self._current_mirror_idx]

    def current_mirror_base(self, path: str = "") -> str:
        """
        生成标准化的镜像基础URL, 确保路径以斜杠结尾

        Args:
            path: 要追加的路径部分

        Returns:
            标准化后的完整URL, 保证以斜杠结尾
        """
        base_url = self._available_mirrors[self._current_mirror_idx]

        parsed = urlparse(base_url)

        base_path = PurePosixPath(parsed.path)
        full_path = Path(base_path / path).resolve(strict=False)

        path_str = str(full_path)
        if not path_str.endswith("/"):
            path_str += "/"

        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                path_str,  # 已确保以斜杠结尾
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )

    @staticmethod
    def parse_package_line(line: str) -> Optional[Tuple[str, str]]:
        """
        Parse a single line from a requirements.txt file to extract package name and version.

        Supports formats like "package==version" or "package[extras]==version".
        Lines starting with '#' or empty lines are ignored.

        For --all-versions mode, version can be ignored but still parsed for compatibility.

        Args:
            line: The line string to parse.

        Returns:
            A tuple containing (package_name, version) if parsing succeeds, None otherwise.
            Version may be empty string if not specified (for --all-versions mode).
        """
        line = line.strip()
        if not line or line.startswith("#"):
            return None

        # Regex to capture package name, optional extras, and version.
        # Group 1: base package name (e.g., "zabbix-utils")
        # Group 2: extras (e.g., "async") - optional
        # Group 3: version (e.g., "3.1")
        match = re.match(r"^([\w\-\.]+)(?:\[([\w,\-]+)\])?==([\w\.\-]+)$", line)
        if match:
            base_package_name: str = match.group(1)
            extras: Optional[str] = match.group(2)
            version: str = match.group(3)

            full_package_name: str
            if extras:
                full_package_name = f"{base_package_name}[{extras}]"
            else:
                full_package_name = base_package_name

            return full_package_name, version

        # For --all-versions mode, also support package name without version
        match_no_version = re.match(r"^([\w\-\.]+)(?:\[([\w,\-]+)\])?$", line)
        if match_no_version:
            base_package_name = match_no_version.group(1)
            extras = match_no_version.group(2)

            full_package_name = (
                f"{base_package_name}[{extras}]" if extras else base_package_name
            )
            return full_package_name, ""  # Empty version for all-versions mode

        return None

    @staticmethod
    def parse_wheel_filename(filename: str) -> Optional[Dict[str, Optional[str]]]:
        """
        Parse a wheel filename according to PEP 425.

        Format: {distribution}-{version}(-{build})?-{python}-{abi}-{platform}.whl

        Args:
            filename: The wheel filename to parse.

        Returns:
            Dict with keys: name, version, build, python, abi, platform, or None if not a wheel.
        """
        if not filename.endswith(".whl"):
            return None

        # Remove .whl extension
        name_parts = filename[:-4].split("-")

        if len(name_parts) < 5:
            return None

        # Handle optional build tag
        if len(name_parts) >= 6:
            # Has build tag
            return {
                "name": name_parts[0],
                "version": name_parts[1],
                "build": name_parts[2],
                "python": name_parts[3],
                "abi": name_parts[4],
                "platform": name_parts[5],
            }
        else:
            # No build tag
            return {
                "name": name_parts[0],
                "version": name_parts[1],
                "build": None,
                "python": name_parts[2],
                "abi": name_parts[3],
                "platform": name_parts[4],
            }

    def matches_filter(
        self,
        filename: str,
        python_version: Optional[str] = None,
        abi: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> bool:
        """
        Check if a file matches the specified filters.

        Args:
            filename: The filename to check.
            python_version: Python version filter (e.g., "cp311", "py3", "py2.py3").
            abi: ABI filter (e.g., "cp311", "abi3", "none").
            platform: Platform filter (e.g., "manylinux_2_17_x86_64", "win_amd64", "any").

        Returns:
            True if file matches all specified filters (or is not a wheel).
        """
        # Non-wheel files (source distributions) always pass
        wheel_info = self.parse_wheel_filename(filename)
        if not wheel_info:
            return True

        # Always ignore Python 2 only packages
        file_python_tags = (wheel_info["python"] or "").split(".")

        # Check if it's Python 2 only (py2, py20, py21, etc. but NOT py2.py3)
        is_py2_only = any(
            tag.startswith("py2") and tag != "py2" for tag in file_python_tags
        ) and not any(
            tag.startswith("py3") or tag.startswith("cp3") for tag in file_python_tags
        )

        # Also check for pure py2 tag without py3
        if "py2" in file_python_tags and not any(
            tag.startswith("py3") or tag.startswith("cp3") for tag in file_python_tags
        ):
            is_py2_only = True

        if is_py2_only:
            logger.debug(f"Skipping Python 2 only package: {filename}")
            return False

        # Check python version filter
        if python_version:
            # Handle compressed tags like "py2.py3"
            filter_python_tags = python_version.split(".")

            # Match if any filter tag is in file tags
            if not any(tag in file_python_tags for tag in filter_python_tags):
                return False

        # Check ABI filter
        if abi:
            file_abi_tags = (wheel_info["abi"] or "").split(".")
            filter_abi_tags = abi.split(".")

            if not any(tag in file_abi_tags for tag in filter_abi_tags):
                return False

        # Check platform filter
        if platform:
            file_platform_tags = (wheel_info["platform"] or "").split(".")
            filter_platform_tags = platform.split(".")

            if not any(tag in file_platform_tags for tag in filter_platform_tags):
                return False

        return True

    async def fetch_metadata(
        self, package_with_extras: str
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch metadata for a package from PyPI mirrors with automatic fallback.

        Args:
            package_with_extras: The package name, potentially with extras.

        Returns:
            The package metadata as a dict if successful, None if all mirrors fail.
        """

        last_exception = None
        package_for_url: str = re.sub(r"\[.*?\]", "", package_with_extras)

        # Try all available mirrors
        max_attempts = len(self._available_mirrors)

        for _ in range(max_attempts):
            current_mirror = self._available_mirrors[self._current_mirror_idx]

            # Determine URL format based on mirror type
            if current_mirror == self.OFFICIAL_PYPI:
                url = f"https://pypi.org/pypi/{package_for_url}/json"
            else:
                url = f"{self.current_mirror_base('web/json/')}/{package_for_url}"

            try:
                logger.debug(f"Trying metadata URL: {url}")
                assert self.session is not None
                async with self.session.get(url, timeout=self.timeout) as resp:
                    content = await resp.read()
                    try:
                        return json.loads(content.decode("utf-8"))
                    except json.JSONDecodeError as e:
                        raise aiohttp.ClientError(f"Invalid JSON: {str(e)}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_exception = e
                logger.warning(
                    f"Mirror {current_mirror} failed: {str(e)}. Trying next mirror..."
                )
                await self.get_next_mirror()
                continue
            # pylint: disable=W0718 # Catching too general exception Exception
            except Exception as e:
                logger.error(
                    f"An unexpected error occurred fetching metadata for "
                    f"{package_with_extras} from {url}: {e}"
                )
                return None

        logger.error(
            f"All mirrors failed for {package_with_extras}: {str(last_exception)}"
        )
        return None

    def find_version_info(
        self, metadata: Dict[str, Any], version: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Find release information for a specific package version.

        Args:
            metadata: The full package metadata.
            version: The version to look up.

        Returns:
            List of release files if version exists, None otherwise.
        """
        return metadata.get("releases", {}).get(version)

    def find_all_python3_versions(
        self, metadata: Dict[str, Any]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Find all Python 3 compatible versions from package metadata.

        Args:
            metadata: The full package metadata.

        Returns:
            Dict mapping version strings to their release file lists.
            Only includes versions that have Python 3 compatible files.
        """
        all_releases = metadata.get("releases", {})
        python3_releases = {}

        for version, files in all_releases.items():
            if not files:
                continue

            # Check if this version has any Python 3 compatible files
            has_py3_files = False
            for file_info in files:
                filename = file_info.get("filename", "")

                # Source distributions are always compatible
                if not filename.endswith(".whl"):
                    has_py3_files = True
                    break

                # Check wheel for Python 3 compatibility
                wheel_info = self.parse_wheel_filename(filename)
                if wheel_info:
                    python_tags = (wheel_info["python"] or "").split(".")
                    # Has py3, py30+, cp3x, or py2.py3 tags
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
        self, versions_dict: Dict[str, List[Dict[str, Any]]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Filter versions to keep only the latest patch version for each minor version.

        For example:
        - 2.1.3, 2.1.5, 2.1.9 → keep only 2.1.9
        - 2.2.2, 2.2.8 → keep only 2.2.8
        - 3.0.0 → keep 3.0.0

        Args:
            versions_dict: Dict mapping version strings to their release file lists.

        Returns:
            Filtered dict with only the latest patch version for each minor version.
        """
        # Group versions by (major, minor)
        versions_by_minor: Dict[Tuple[int, int], List[Tuple[Version, str]]] = {}

        for version_str in versions_dict.keys():
            try:
                version_obj = Version(version_str)
                # Group by (major, minor)
                # For pre-release versions, still use their base version for grouping
                key = (version_obj.major, version_obj.minor)

                if key not in versions_by_minor:
                    versions_by_minor[key] = []

                versions_by_minor[key].append((version_obj, version_str))
            except InvalidVersion:
                # If version can't be parsed, keep it (better safe than sorry)
                logger.warning(
                    f"Could not parse version '{version_str}', keeping it anyway"
                )
                continue

        # For each minor version group, keep only the latest
        filtered_versions = {}
        for minor_key, version_list in versions_by_minor.items():
            # Sort by version object (PEP 440 compliant)
            version_list.sort(key=lambda x: x[0], reverse=True)
            # Take the highest version
            latest_version_obj, latest_version_str = version_list[0]

            filtered_versions[latest_version_str] = versions_dict[latest_version_str]

            # Log what we're keeping
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

    def _count_downloadable_files(self, metadata: Dict[str, Any], version: str) -> int:
        """
        Count the number of files that would be downloaded for a package.

        Args:
            metadata: The full package metadata.
            version: The version to count files for (ignored if all_versions=True or latest_patch=True).

        Returns:
            Integer count of files that match filters and would be downloaded.
        """
        count = 0
        skipped_py2 = 0

        # Determine which versions to count
        if self.all_versions or self.latest_patch:
            versions_to_count = self.find_all_python3_versions(metadata)
            # Apply latest-patch filter if enabled
            if self.latest_patch:
                versions_to_count = self.filter_latest_patch_versions(versions_to_count)
        else:
            version_info = self.find_version_info(metadata, version)
            if not version_info:
                return 0
            versions_to_count = {version: version_info}

        # Count files that match filters
        for ver, version_files in versions_to_count.items():
            for file_info in version_files:
                filename = file_info.get("filename", "")

                # Apply filters (includes Python 2 filtering)
                if self.matches_filter(
                    filename, self.python_version, self.abi, self.platform
                ):
                    count += 1
                else:
                    # Check if it was skipped due to Python 2
                    wheel_info = self.parse_wheel_filename(filename)
                    if wheel_info:
                        file_python_tags = (wheel_info["python"] or "").split(".")
                        is_py2_only = "py2" in file_python_tags and not any(
                            tag.startswith("py3") or tag.startswith("cp3")
                            for tag in file_python_tags
                        )
                        if is_py2_only:
                            skipped_py2 += 1

        if skipped_py2 > 0:
            logger.debug(f"Skipped {skipped_py2} Python 2 only files in count")

        return count

    def rewrite_url(self, url: str) -> str:
        """
        Rewrite a PyPI download URL to use the current mirror.

        Args:
            url: The original download URL.

        Returns:
            The rewritten URL pointing to the current mirror, or original URL for official PyPI.
        """
        current_mirror = self._available_mirrors[self._current_mirror_idx]

        # If using official PyPI, return original URL
        if current_mirror == self.OFFICIAL_PYPI:
            return url

        # For CN mirrors, rewrite the URL
        if url.startswith("https://files.pythonhosted.org/"):
            return url.replace(
                "https://files.pythonhosted.org/packages/",
                self.current_mirror_base("web/packages/"),
            )
        return url

    @staticmethod
    def get_filename_from_url(url: str) -> str:
        """
        Extract filename from a URL.

        Args:
            url: The URL to parse.

        Returns:
            The filename part of the URL.
        """
        return url.split("/")[-1]

    @staticmethod
    def compute_hash(file_path: Path, algo: str = "sha256") -> str:
        """
        Compute cryptographic hash of a file.

        Args:
            file_path: Path to the file.
            algo: Hash algorithm to use.

        Returns:
            The hexadecimal hash digest.
        """
        h = hashlib.new(algo)
        with file_path.open("rb") as f:
            # Read file in chunks to handle large files efficiently without
            # loading entire file into memory
            while chunk := f.read(8192):  # Read 8KB chunks
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    async def compute_hash_async(file_path: Path, algo: str = "sha256") -> str:
        """
        Compute cryptographic hash of a file asynchronously using thread pool.

        Args:
            file_path: Path to the file.
            algo: Hash algorithm to use.

        Returns:
            The hexadecimal hash digest.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,  # Use default ThreadPoolExecutor
            PackageDownloader.compute_hash,
            file_path,
            algo,
        )

    def _init_progress_bar(self, total: int) -> None:
        """Initialize progress tracking with total file count."""
        try:
            self.total_files = total
            self.completed_files = 0
            if self.rich_sink:
                self.rich_sink.init_progress(total)
            logger.info(f"Initialized progress tracking with {total} total files")
        except Exception as e:
            logger.warning(
                f"Failed to initialize progress tracking: {e}. Continuing with log-only mode."
            )

    def _update_progress(self, n: int = 1) -> None:
        """Update progress by n files."""
        try:
            self.completed_files += n
            if self.rich_sink:
                self.rich_sink.update_progress(n)
        except Exception as e:
            logger.debug(f"Progress update failed: {e}")

    def _close_progress_bar(self) -> None:
        """Close progress tracking."""
        try:
            # Progress bar will be stopped when rich_sink is stopped
            pass
        except Exception as e:
            logger.debug(f"Progress close failed: {e}")

    async def download_file(
        self, url: str, filename: str, expected_hash: Optional[str] = None
    ) -> bool:
        """
        Download a file with retry logic, mirror switching, and hash verification.

        Args:
            url: The file URL to download.
            filename: The target filename.
            expected_hash: Expected SHA-256 hash from PyPI API (format: "sha256=...").

        Returns:
            True if download succeeded or file exists with matching hash.
        """
        dest_path: Path = self.download_dir / filename

        # Check if file already exists (async)
        loop = asyncio.get_event_loop()
        file_exists = await loop.run_in_executor(None, dest_path.exists)

        if file_exists:
            if expected_hash:
                # Extract hash value from "sha256=..." format
                if expected_hash.startswith("sha256="):
                    expected_hash_value = expected_hash[7:]  # Remove "sha256=" prefix
                else:
                    expected_hash_value = expected_hash

                # Compute hash of existing file asynchronously
                existing_hash = await self.compute_hash_async(dest_path)

                if existing_hash == expected_hash_value:
                    logger.debug(f"File exists with valid hash, skipping: {filename}")
                    self._update_progress(1)  # Update progress for skipped file
                    return True
                else:
                    logger.warning(
                        f"File exists but hash mismatch, re-downloading: {filename}"
                    )
                    # Continue to download
            else:
                # No hash provided, trust existing file
                logger.debug(f"File already exists, skipping download: {filename}")
                self._update_progress(1)  # Update progress for skipped file
                return True

        # Save current mirror index to restore later
        original_mirror_idx = self._current_mirror_idx
        mirror_attempts = 0  # Track attempts on current mirror

        for attempt in range(1, self.DEFAULT_RETRIES + 1):
            # Rewrite URL with current mirror
            rewritten_url: str = self.rewrite_url(url)

            # Log the URL being downloaded (file only, not to screen)
            logger.opt(depth=1).log("TRACE", f"Downloading: {rewritten_url}")

            try:
                assert self.session is not None, "ClientSession is not initialized."
                async with self.session.get(
                    rewritten_url, timeout=self.timeout
                ) as resp:
                    resp.raise_for_status()  # Check for HTTP errors (4xx/5xx)
                    content: bytes = await resp.read()

                    # Verify hash if provided (in memory, fast)
                    if expected_hash:
                        if expected_hash.startswith("sha256="):
                            expected_hash_value = expected_hash[7:]
                        else:
                            expected_hash_value = expected_hash

                        downloaded_hash = hashlib.sha256(content).hexdigest()

                        if downloaded_hash != expected_hash_value:
                            logger.error(
                                f"Hash verification failed for {filename}: "
                                f"expected {expected_hash_value}, got {downloaded_hash}"
                            )
                            # Restore original mirror index
                            self._current_mirror_idx = original_mirror_idx
                            return False

                    # Ensure directory exists (async)
                    await loop.run_in_executor(
                        None,
                        lambda: self.download_dir.mkdir(parents=True, exist_ok=True),
                    )

                    # Write the downloaded content to file (async)
                    await loop.run_in_executor(None, dest_path.write_bytes, content)

                    logger.info(f"Downloaded: {filename}")
                    self._update_progress(1)  # Update progress for successful download
                    # Restore original mirror index
                    self._current_mirror_idx = original_mirror_idx
                    return True

            except aiohttp.ClientError as e:
                mirror_attempts += 1
                logger.warning(
                    f"Attempt {attempt}/{self.DEFAULT_RETRIES} (mirror attempt {mirror_attempts}/{self.RETRIES_PER_MIRROR}): "
                    f"Client error downloading {rewritten_url}: {e}"
                )

                # Switch mirror after RETRIES_PER_MIRROR attempts
                if mirror_attempts >= self.RETRIES_PER_MIRROR:
                    old_mirror = self._available_mirrors[self._current_mirror_idx]
                    await self.get_next_mirror()
                    new_mirror = self._available_mirrors[self._current_mirror_idx]
                    logger.info(
                        f"Switching mirror for {filename}: {old_mirror} → {new_mirror}"
                    )
                    mirror_attempts = 0  # Reset counter for new mirror

            except asyncio.TimeoutError:
                mirror_attempts += 1
                logger.warning(
                    f"Attempt {attempt}/{self.DEFAULT_RETRIES} (mirror attempt {mirror_attempts}/{self.RETRIES_PER_MIRROR}): "
                    f"Timeout (no data received for {self.timeout.sock_read}s) downloading {rewritten_url}"
                )

                # Switch mirror after RETRIES_PER_MIRROR attempts
                if mirror_attempts >= self.RETRIES_PER_MIRROR:
                    old_mirror = self._available_mirrors[self._current_mirror_idx]
                    await self.get_next_mirror()
                    new_mirror = self._available_mirrors[self._current_mirror_idx]
                    logger.info(
                        f"Switching mirror for {filename}: {old_mirror} → {new_mirror}"
                    )
                    mirror_attempts = 0  # Reset counter for new mirror

            # pylint: disable=W0718 # Catching too general exception Exception
            except Exception as e:
                mirror_attempts += 1
                logger.warning(
                    f"Attempt {attempt}/{self.DEFAULT_RETRIES} (mirror attempt {mirror_attempts}/{self.RETRIES_PER_MIRROR}): "
                    f"An unexpected error downloading {rewritten_url}: {e}"
                )

                # Switch mirror after RETRIES_PER_MIRROR attempts
                if mirror_attempts >= self.RETRIES_PER_MIRROR:
                    old_mirror = self._available_mirrors[self._current_mirror_idx]
                    await self.get_next_mirror()
                    new_mirror = self._available_mirrors[self._current_mirror_idx]
                    logger.info(
                        f"Switching mirror for {filename}: {old_mirror} → {new_mirror}"
                    )
                    mirror_attempts = 0  # Reset counter for new mirror

        logger.error(
            f"Failed to download after {self.DEFAULT_RETRIES} retries: {filename}"
        )
        self._update_progress(1)  # Update progress even for failed downloads
        # Restore original mirror index
        self._current_mirror_idx = original_mirror_idx
        return False

    async def process_package(self, line: str) -> Dict[str, Any]:
        """
        Process a single package definition from requirements.txt.

        Args:
            line: The line from requirements.txt.

        Returns:
            Dictionary containing package processing status.
        """
        # parse_package_line is now called within run() to filter lines before
        # task creation, so this method will only receive valid lines that can
        # be parsed.
        parsed: Optional[Tuple[str, str]] = self.parse_package_line(line)
        if not parsed:
            # This case should ideally not be hit if lines are pre-filtered,
            # but included for robustness.
            return {
                "package": line.strip() if line.strip() else "N/A",
                "version": "N/A",
                "status": "Error (Pre-filter)",
                "details": "Unexpected unparsable line",
            }
        name, version = parsed
        package_status: Dict[str, Any] = {
            "package": name,
            "version": version,
            "status": "Failed",
            "details": "",
        }

        async with self.semaphore:
            try:
                metadata: Optional[Dict[str, Any]] = await self.fetch_metadata(name)
                if not metadata:
                    package_status["details"] = "Failed to fetch metadata"
                    return package_status

                # Determine which versions to download
                versions_to_download: Dict[str, List[Dict[str, Any]]] = {}

                if self.all_versions or self.latest_patch:
                    # Download all Python 3 compatible versions
                    versions_to_download = self.find_all_python3_versions(metadata)
                    if not versions_to_download:
                        package_status["details"] = (
                            "No Python 3 compatible versions found"
                        )
                        return package_status

                    # Apply latest-patch filter if enabled
                    if self.latest_patch:
                        versions_to_download = self.filter_latest_patch_versions(
                            versions_to_download
                        )
                        package_status["version"] = (
                            f"latest-patch ({len(versions_to_download)} versions)"
                        )
                    else:
                        package_status["version"] = (
                            f"all ({len(versions_to_download)} versions)"
                        )
                else:
                    # Download only the specified version
                    version_info: Optional[List[Dict[str, Any]]] = (
                        self.find_version_info(metadata, version)
                    )
                    if not version_info:
                        package_status["details"] = "No release info found"
                        return package_status
                    versions_to_download[version] = version_info

                download_success_count = 0
                # Count only files that will actually be downloaded (after filtering)
                total_files = 0
                for ver, version_files in versions_to_download.items():
                    for file_info in version_files:
                        file_name = file_info.get("filename", "")
                        if self.matches_filter(
                            file_name, self.python_version, self.abi, self.platform
                        ):
                            total_files += 1

                # Collect all download tasks for concurrent execution
                download_tasks = []
                for ver, version_files in versions_to_download.items():
                    for file_info in version_files:
                        url: str = file_info["url"]
                        filename: str = file_info["filename"]

                        # Get hash from PyPI API (digests field)
                        expected_hash: Optional[str] = None
                        if "digests" in file_info and "sha256" in file_info["digests"]:
                            expected_hash = f"sha256={file_info['digests']['sha256']}"

                        # Apply filters
                        if not self.matches_filter(
                            filename, self.python_version, self.abi, self.platform
                        ):
                            logger.debug(f"Skipping {filename} (doesn't match filters)")
                            continue

                        final_url: str = self.rewrite_url(url)
                        self.download_urls.append(final_url)

                        if self.dry_run:
                            logger.info(f"[Dry-run] Would download: {final_url}")
                            download_success_count += 1  # Count as success in dry-run
                        else:
                            # Add download task for concurrent execution
                            download_tasks.append(
                                self.download_file(final_url, filename, expected_hash)
                            )

                # Execute all downloads concurrently
                if download_tasks:
                    download_results = await asyncio.gather(*download_tasks)
                    download_success_count = sum(
                        1 for result in download_results if result
                    )

                if total_files > 0 and download_success_count == total_files:
                    package_status["status"] = "Synchronized"
                    package_status["details"] = f"All {total_files} file(s) processed"
                elif total_files > 0 and download_success_count > 0:
                    package_status["status"] = "Partial Sync"
                    package_status["details"] = (
                        f"{download_success_count}/{total_files} file(s) processed"
                    )
                elif total_files == 0:
                    package_status["status"] = "No Files"
                    package_status["details"] = (
                        "No downloadable files found for this version"
                    )
                else:
                    package_status["status"] = "Failed"
                    package_status["details"] = "No files downloaded"
            # pylint: disable=W0718 # Catching too general exception Exception
            except Exception as e:
                logger.exception(
                    f"An unhandled error occurred while processing package "
                    f"line '{line.strip()}': {e}"
                )
                package_status["details"] = f"Unhandled error: {e}"
        return package_status

    async def run(self) -> List[Dict[str, Any]]:
        """
        Main execution method to process all packages.

        Returns:
            List of package status dictionaries.
        """
        logger.info(f"Starting package download process")
        self.download_dir.mkdir(parents=True, exist_ok=True)

        # Set User-Agent to mimic pip for better compatibility with PyPI mirrors
        headers = {
            "User-Agent": f"pip/24.0 (python {'.'.join(map(str, __import__('sys').version_info[:3]))})"
        }

        # For I/O-bound operations (file read/write), ThreadPoolExecutor is actually better:
        # - File I/O releases GIL, so threads can work in parallel
        # - No process creation overhead
        # - Shared memory (no pickling overhead)
        import os

        max_workers = min(32, (os.cpu_count() or 1) * 4)
        loop = asyncio.get_event_loop()
        executor = ThreadPoolExecutor(max_workers=max_workers)
        loop.set_default_executor(executor)

        logger.info(
            f"Using {max_workers} threads for I/O operations (file I/O releases GIL)"
        )

        async with aiohttp.ClientSession(headers=headers) as self.session:
            # Parse valid lines from requirements content
            logger.debug(f"Parsing requirements from resolved dependencies")
            valid_lines: List[str] = []
            for line in self.requirements_content.splitlines():
                stripped_line = line.strip()
                # Only add lines that are not empty and not comments
                if stripped_line and not stripped_line.startswith("#"):
                    # Further check if it's a parseable package line
                    if self.parse_package_line(line):
                        valid_lines.append(line)
                    else:
                        logger.warning(f"Skipping unparseable line: {line.strip()}")

            # Phase 1: Fetch metadata and count total files
            logger.info("Phase 1: Fetching package metadata and counting files...")
            package_metadata_list: List[Tuple[str, Optional[Dict[str, Any]], int]] = []
            total_files = 0

            for line in valid_lines:
                parsed = self.parse_package_line(line)
                if not parsed:
                    continue

                name, version = parsed
                logger.debug(f"Fetching metadata for {name}...")
                metadata = await self.fetch_metadata(name)

                if metadata:
                    file_count = self._count_downloadable_files(metadata, version)
                    total_files += file_count
                    package_metadata_list.append((line, metadata, file_count))
                    logger.debug(f"{name}: {file_count} files to download")
                else:
                    logger.warning(f"Failed to fetch metadata for {name}")
                    package_metadata_list.append((line, None, 0))

            logger.info(f"Total files to download: {total_files}")

            # Initialize progress bar with total file count
            self._init_progress_bar(total_files)

            # Phase 2: Download files
            logger.info("Phase 2: Downloading files...")
            all_package_results: List[Dict[str, Any]] = await asyncio.gather(
                *(self.process_package(line) for line in valid_lines)
            )

            # Close progress bar
            self._close_progress_bar()

            # Save URL list in dry-run mode
            if self.dry_run and self.download_urls:
                self.url_list_file.write_text(
                    "\n".join(self.download_urls), encoding="utf-8"
                )
                logger.info(f"✔ URL list saved to {self.url_list_file}")
            elif self.dry_run:
                logger.info(
                    "No download URLs were collected. URL list file "
                    "will not be created."
                )

        # Shutdown executor
        executor.shutdown(wait=True)

        return all_package_results  # Return the collected results


def configure_logging(use_rich: bool = False) -> Optional[RichLogSink]:
    """Configure loguru with optional Rich Live display and file logging."""
    # Remove default handler
    logger.remove()

    rich_sink = None

    if use_rich:
        # Create Rich sink for terminal display (20 lines + 1 progress line = 21 total)
        rich_sink = RichLogSink(max_lines=20)
        rich_sink.start()

        # Add Rich sink (DEBUG+)
        logger.add(
            rich_sink,
            level="DEBUG",
            format="{time:HH:mm:ss} | {level: <8} | {message}",
        )
    else:
        # Add simple console sink for initial setup (INFO+)
        logger.add(
            sys.stderr,
            level="INFO",
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
            colorize=True,
        )

    # Add file sink (TRACE+, with rotation) - always enabled, captures everything
    logger.add(
        "./pypi-downloader.log",
        level="TRACE",
        rotation="10 MB",
        retention=3,
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    )

    return rich_sink


def main() -> None:
    """Main entry point for command-line execution."""
    # Start with simple console logging for initial setup
    configure_logging(use_rich=False)

    parser = argparse.ArgumentParser(description="PyPI Package Downloader")
    parser.add_argument(
        "requirements",
        type=str,
        nargs="?",
        default=None,
        help="Path to the requirements.txt file",
    )
    parser.add_argument(
        "-r",
        "--requirement",
        type=str,
        dest="requirement_file",
        help="Path to the requirements.txt file (alternative to positional argument)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only generate URL list without downloading",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=16,
        help="Maximum concurrent downloads (default: 16)",
    )
    parser.add_argument(
        "--download-dir",
        type=str,
        default=str(Path.cwd() / "pypi"),
        help="Directory to save downloads (default: ./pypi)",
    )
    parser.add_argument(
        "--cn",
        action="store_true",
        help="Use Chinese PyPI mirrors with automatic fallback (default: use official PyPI)",
    )
    parser.add_argument(
        "--build-index",
        action="store_true",
        help="Build PyPI-compatible index using dir2pi after downloading",
    )
    parser.add_argument(
        "--python-version",
        type=str,
        help="Filter by Python version tag (e.g., cp311, py3, py2.py3)",
    )
    parser.add_argument(
        "--abi",
        type=str,
        help="Filter by ABI tag (e.g., cp311, abi3, none)",
    )
    parser.add_argument(
        "--platform",
        type=str,
        help="Filter by platform tag (e.g., manylinux_2_17_x86_64, win_amd64, any)",
    )
    parser.add_argument(
        "--all-versions",
        action="store_true",
        help="Download all available Python 3 versions of each package (ignores version pins in requirements.txt)",
    )
    parser.add_argument(
        "--latest-patch",
        action="store_true",
        help="Download only the latest patch version for each minor version (e.g., keep 2.1.9 from 2.1.3, 2.1.5, 2.1.9). Mutually exclusive with --all-versions",
    )
    parser.add_argument(
        "--url-list-path",
        type=str,
        help="Custom path for URL list file (default: ./url_list.txt, only used in dry-run mode)",
    )

    args = parser.parse_args()

    # Validate argument combinations
    if args.latest_patch and args.all_versions:
        logger.error(
            "--latest-patch and --all-versions are mutually exclusive. "
            "Use --latest-patch alone to download only latest patch versions, "
            "or --all-versions to download all versions."
        )
        parser.print_help()
        return

    # Determine requirements file path
    requirements_path = None
    if args.requirement_file:
        requirements_path = args.requirement_file
    elif args.requirements:
        requirements_path = args.requirements
    else:
        # Default to requirements.txt in current directory
        requirements_path = str(Path.cwd() / "requirements.txt")

    if not Path(requirements_path).exists():
        logger.error(f"Requirements file not found: {requirements_path}")
        parser.print_help()
        return

    download_dir = Path(args.download_dir)

    # Determine URL list path
    url_list_path = Path(args.url_list_path) if args.url_list_path else None

    # Always resolve dependencies with pip-compile
    logger.info("=" * 60)
    logger.info("Resolving dependencies with pip-compile...")
    logger.info("=" * 60)

    logger.info(f"Input file: {requirements_path}")

    if args.all_versions:
        logger.info(
            "Note: --all-versions is enabled, version pins will be ignored during download"
        )

    resolved_content = None  # Will store resolved dependencies in memory

    try:
        # Build pip-compile command (output to stdout using -o -)
        pip_compile_cmd = [
            "pip-compile",
            str(requirements_path),
            "-o",
            "-",  # Output to stdout
            "--no-header",
            # No --verbose: reduce output noise
        ]

        # Add index URL if using CN mirrors
        if args.cn:
            # Use first CN mirror for dependency resolution
            mirror_url = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
            logger.info(f"Using Chinese mirror for resolution: {mirror_url}")
            pip_compile_cmd.extend(["-i", mirror_url])
        else:
            logger.info("Using official PyPI for dependency resolution")

        logger.info(f"Running command: {' '.join(pip_compile_cmd)}")
        logger.info("This may take a while depending on the number of packages...")

        result = subprocess.run(
            pip_compile_cmd, capture_output=True, text=True, check=True
        )

        # Store resolved content in memory
        resolved_content = result.stdout

        # Log pip-compile stderr (errors/warnings) only
        if result.stderr:
            logger.debug("pip-compile stderr:")
            for line in result.stderr.strip().split("\n"):
                logger.debug(f"  {line}")

        # Count resolved packages
        resolved_lines = [
            line
            for line in resolved_content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        logger.info("=" * 60)
        logger.info(f"✔ Dependencies resolved successfully!")
        logger.info(f"✔ Resolved {len(resolved_lines)} packages in memory")
        if args.all_versions:
            logger.info("✔ Will download all Python 3 versions of resolved packages")
        logger.info("=" * 60)

    except FileNotFoundError:
        logger.error("=" * 60)
        logger.error("❌ pip-compile command not found!")
        logger.error("Please install pip-tools: pip install pip-tools")
        logger.error("=" * 60)
        return
    except subprocess.CalledProcessError as e:
        logger.error("=" * 60)
        logger.error("❌ Failed to resolve dependencies!")
        logger.error(f"Error: {e.stderr}")
        logger.error("=" * 60)
        return
    # pylint: disable=W0718 # Catching too general exception Exception
    except Exception as e:
        logger.error("=" * 60)
        logger.error(f"❌ Unexpected error resolving dependencies: {e}")
        logger.error("=" * 60)
        return

    logger.info(f"Packages will be downloaded to: {download_dir.absolute()}")

    # Pass the resolved content to PackageDownloader
    downloader = PackageDownloader(
        requirements_content=resolved_content,
        dry_run=args.dry_run,
        concurrency=args.concurrency,
        download_dir=download_dir,
        use_cn_mirrors=args.cn,
        python_version=args.python_version,
        abi=args.abi,
        platform=args.platform,
        all_versions=args.all_versions,
        latest_patch=args.latest_patch,
        url_list_path=url_list_path,
    )

    # Now switch to Rich Live display for download progress
    logger.info("=" * 60)
    logger.info("Switching to live progress display...")
    logger.info("=" * 60)

    # Reconfigure logging with Rich
    rich_sink = configure_logging(use_rich=True)

    # Set rich_sink for progress tracking
    downloader.rich_sink = rich_sink

    package_sync_results: List[Dict[str, Any]] = asyncio.run(downloader.run())

    # Stop rich sink before showing final table
    if rich_sink:
        rich_sink.stop()

    console = Console()
    table = Table(
        title="Package Synchronization Summary",
        show_header=True,
        header_style="bold magenta",
    )

    table.add_column("Package", style="cyan", no_wrap=True)
    table.add_column("Version", style="green")
    table.add_column("Status", justify="center", style="bold")
    table.add_column("Details", style="dim")

    for pkg_result in package_sync_results:
        package = pkg_result.get("package", "N/A")
        version = pkg_result.get("version", "N/A")
        status = pkg_result.get("status", "Unknown")
        details = pkg_result.get("details", "")

        status_style = ""
        if status == "Synchronized":
            status_style = "bold green"
        elif status == "Partial Sync":
            status_style = "bold yellow"
        elif status == "Failed":
            status_style = "bold red"
        elif status == "No Files":
            status_style = "blue"
        elif status == "Error (Pre-filter)":
            status_style = "bold red on black"

        table.add_row(package, version, f"[{status_style}]{status}[/]", details)

    console.print(table)

    # Build PyPI index if requested
    if args.build_index and not args.dry_run:
        logger.info("Building PyPI-compatible index with dir2pi...")
        try:
            result = subprocess.run(
                ["dir2pi", str(download_dir)],
                capture_output=True,
                text=True,
                check=True,
            )
            logger.info(f"Index built successfully at {download_dir}/simple/")
            if result.stdout:
                logger.debug(result.stdout)
        except FileNotFoundError:
            logger.error(
                "dir2pi command not found. Please install pip2pi: pip install pip2pi"
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to build index: {e.stderr}")
        # pylint: disable=W0718 # Catching too general exception Exception
        except Exception as e:
            logger.error(f"Unexpected error building index: {e}")


if __name__ == "__main__":
    main()
