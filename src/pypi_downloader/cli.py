"""
pypi_downloader.py - A Python script to download packages from PyPI mirrors
with automatic fallback mechanism when mirrors fail.
"""

import argparse
import asyncio
import hashlib
import json
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
from rich.console import Console, Group
from rich.live import Live
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
from rich.table import Table
from rich.text import Text
from rich.panel import Panel


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
                vertical_overflow="visible"
            )
            self.live.start()

    def write(self, message):
        """写入日志消息"""
        # 截断过长的消息（避免换行）
        max_width = 120  # 最大宽度
        msg = message.rstrip()
        if len(msg) > max_width:
            msg = msg[:max_width-3] + "..."
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
            return Group(
                Text(log_text),
                Text(separator, style="dim"),
                self.progress
            )
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

    DEFAULT_CONCURRENCY: int = 256
    DEFAULT_RETRIES: int = 5

    def __init__(
        self,
        requirements_file: Path,
        dry_run: bool = False,
        concurrency: int = DEFAULT_CONCURRENCY,
        download_dir: Path = Path.cwd() / "pypi",
        use_cn_mirrors: bool = False,
        python_version: Optional[str] = None,
        abi: Optional[str] = None,
        platform: Optional[str] = None,
        all_versions: bool = False,
        save_url_list: bool = False,
        url_list_path: Optional[Path] = None,
    ) -> None:
        """
        Initialize the PackageDownloader.

        Args:
            requirements_file: Path to the requirements.txt file.
            dry_run: If True, only generates URL list without downloading.
            concurrency: Maximum number of concurrent downloads.
            download_dir: Directory to save downloaded packages.
            use_cn_mirrors: If True, use Chinese mirrors with fallback. Otherwise use official PyPI.
            python_version: Python version filter (e.g., "cp311", "py3").
            abi: ABI filter (e.g., "cp311", "abi3", "none").
            platform: Platform filter (e.g., "manylinux_2_17_x86_64", "win_amd64", "any").
            all_versions: If True, download all available versions of each package (Python 3 only).
            save_url_list: If True, save list of downloaded URLs to a file.
            url_list_path: Path to save URL list. Defaults to ./url_list.txt in current directory.
        """
        self.requirements_file: Path = requirements_file
        self.session: Optional[aiohttp.ClientSession] = None
        self.dry_run: bool = dry_run
        self.semaphore: asyncio.Semaphore = asyncio.Semaphore(concurrency)
        self.download_urls: List[str] = []
        self.download_dir = download_dir
        self.save_url_list = save_url_list
        self.url_list_file = (
            url_list_path if url_list_path else Path.cwd() / "url_list.txt"
        )
        self.timeout: aiohttp.ClientTimeout = aiohttp.ClientTimeout(
            total=None, connect=60, sock_read=60
        )
        self.use_cn_mirrors = use_cn_mirrors
        self._current_mirror_idx: int = 0
        self.python_version = python_version
        self.abi = abi
        self.platform = platform
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
            logger.info(f"Using Chinese mirrors with fallback")
        else:
            logger.info(f"Using official PyPI (https://pypi.org)")

        if all_versions:
            logger.info(
                "All versions mode enabled: downloading all Python 3 versions of each package"
            )

        if save_url_list:
            logger.info(f"URL list will be saved to: {self.url_list_file}")

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
            self.PYPI_MIRRORS
        )
        return self.PYPI_MIRRORS[self._current_mirror_idx]

    def current_mirror_base(self, path: str = "") -> str:
        """
        生成标准化的镜像基础URL, 确保路径以斜杠结尾

        Args:
            path: 要追加的路径部分

        Returns:
            标准化后的完整URL, 保证以斜杠结尾
        """
        if self.use_cn_mirrors:
            base_url = self.PYPI_MIRRORS[self._current_mirror_idx]
        else:
            base_url = "https://pypi.org"

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
    def parse_wheel_filename(filename: str) -> Optional[Dict[str, str]]:
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
        file_python_tags = wheel_info["python"].split(".")

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
            file_abi_tags = wheel_info["abi"].split(".")
            filter_abi_tags = abi.split(".")

            if not any(tag in file_abi_tags for tag in filter_abi_tags):
                return False

        # Check platform filter
        if platform:
            file_platform_tags = wheel_info["platform"].split(".")
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

        # If not using CN mirrors, only try once with official PyPI
        max_attempts = len(self.PYPI_MIRRORS) if self.use_cn_mirrors else 1

        for _ in range(max_attempts):
            url = (
                f"{self.current_mirror_base('web/json/')}/{package_for_url}"
                if self.use_cn_mirrors
                else f"https://pypi.org/pypi/{package_for_url}/json"
            )
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
                if self.use_cn_mirrors:
                    logger.warning(
                        f"Mirror {url} failed: {str(e)}. " f"Trying next mirror..."
                    )
                    await self.get_next_mirror()
                    continue
                else:
                    logger.error(f"Official PyPI failed: {str(e)}")
                    return None
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
                    python_tags = wheel_info["python"].split(".")
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

    def _count_downloadable_files(
        self, metadata: Dict[str, Any], version: str
    ) -> int:
        """
        Count the number of files that would be downloaded for a package.

        Args:
            metadata: The full package metadata.
            version: The version to count files for (ignored if all_versions=True).

        Returns:
            Integer count of files that match filters and would be downloaded.
        """
        count = 0
        skipped_py2 = 0

        # Determine which versions to count
        if self.all_versions:
            versions_to_count = self.find_all_python3_versions(metadata)
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
                        file_python_tags = wheel_info["python"].split(".")
                        is_py2_only = ("py2" in file_python_tags and not any(
                            tag.startswith("py3") or tag.startswith("cp3") 
                            for tag in file_python_tags
                        ))
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
            The rewritten URL pointing to the current mirror (if using CN mirrors), or original URL.
        """
        if self.use_cn_mirrors and url.startswith("https://files.pythonhosted.org/"):
            # Replace the official PyPI download host with the mirror's
            # equivalent path
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
        Download a file with retry logic and hash verification.

        Args:
            url: The file URL to download.
            filename: The target filename.
            expected_hash: Expected SHA-256 hash from PyPI API (format: "sha256=...").

        Returns:
            True if download succeeded or file exists with matching hash.
        """
        dest_path: Path = self.download_dir / filename
        rewritten_url: str = self.rewrite_url(url)

        # Log the URL being downloaded (file only, not to screen)
        logger.opt(depth=1).log("TRACE", f"Downloading: {rewritten_url}")

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

        for attempt in range(1, self.DEFAULT_RETRIES + 1):
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
                    return True
            except aiohttp.ClientError as e:
                logger.warning(
                    f"Attempt {attempt}/{self.DEFAULT_RETRIES}: Client error "
                    f"downloading {rewritten_url}: {e}"
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"Attempt {attempt}/{self.DEFAULT_RETRIES}: Timeout (no data "
                    f"received for {self.timeout.sock_read}s) downloading "
                    f"{rewritten_url}"
                )
            # pylint: disable=W0718 # Catching too general exception Exception
            except Exception as e:
                logger.warning(
                    f"Attempt {attempt}/{self.DEFAULT_RETRIES}: An unexpected "
                    f"error downloading {rewritten_url}: {e}"
                )
        logger.error(
            f"Failed to download after {self.DEFAULT_RETRIES} retries: {filename}"
        )
        self._update_progress(1)  # Update progress even for failed downloads
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

                if self.all_versions:
                    # Download all Python 3 compatible versions
                    versions_to_download = self.find_all_python3_versions(metadata)
                    if not versions_to_download:
                        package_status["details"] = (
                            "No Python 3 compatible versions found"
                        )
                        return package_status
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
                        filename = file_info.get("filename", "")
                        if self.matches_filter(
                            filename, self.python_version, self.abi, self.platform
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
        logger.info(
            f"Starting package download process from: " f"{self.requirements_file}"
        )
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
            # Parse valid lines from requirements file
            logger.debug(f"Reading requirements from: {self.requirements_file.absolute()}")
            valid_lines: List[str] = []
            with self.requirements_file.open("r", encoding="utf-8") as f:
                for line in f:
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
            package_metadata_list = []
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

            if self.save_url_list and self.download_urls:
                self.url_list_file.write_text(
                    "\n".join(self.download_urls), encoding="utf-8"
                )
                logger.info(f"✔ URL list saved to {self.url_list_file}")
            elif self.save_url_list:
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
        default=256,
        help="Maximum concurrent downloads (default: 256)",
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
        "--resolve-deps",
        action="store_true",
        help="Use pip-compile to resolve dependencies before downloading (requires pip-tools)",
    )
    parser.add_argument(
        "--all-versions",
        action="store_true",
        help="Download all available Python 3 versions of each package (ignores version pins in requirements.txt)",
    )
    parser.add_argument(
        "--save-url-list",
        action="store_true",
        help="Save list of downloaded URLs to a file (default: ./url_list.txt)",
    )
    parser.add_argument(
        "--url-list-path",
        type=str,
        help="Custom path for URL list file (default: ./url_list.txt)",
    )

    args = parser.parse_args()

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

    # Resolve dependencies with pip-compile if requested
    final_requirements_path = Path(requirements_path)
    if args.resolve_deps:
        logger.info("=" * 60)
        logger.info("Starting dependency resolution with pip-compile...")
        logger.info("=" * 60)
        
        # Generate resolved file name: original_name.txt -> original_name.txt.tmp
        resolved_file = Path(requirements_path).parent / f"{Path(requirements_path).name}.tmp"
        
        logger.info(f"Input file: {requirements_path}")
        logger.info(f"Output file: {resolved_file}")
        
        if args.all_versions:
            logger.info("Note: --all-versions is enabled, version pins will be ignored during download")

        try:
            # Build pip-compile command
            pip_compile_cmd = [
                "pip-compile",
                str(requirements_path),
                "-o",
                str(resolved_file),
                "--no-header",
                "--verbose",  # Changed from --quiet to --verbose for logging
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
            
            # Log pip-compile stderr (errors/warnings) only
            if result.stderr:
                logger.debug("pip-compile stderr:")
                for line in result.stderr.strip().split('\n'):
                    logger.debug(f"  {line}")
            
            logger.info("=" * 60)
            logger.info(f"✔ Dependencies resolved successfully!")
            logger.info(f"✔ Resolved file saved to: {resolved_file}")
            if args.all_versions:
                logger.info("✔ Will download all Python 3 versions of resolved packages")
            logger.info("=" * 60)
            
            final_requirements_path = resolved_file

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
    elif args.all_versions:
        logger.info("All versions mode enabled: downloading all Python 3 versions of each package")

    logger.info(f"Packages will be downloaded to: {download_dir.absolute()}")
    logger.info(f"Using requirements file: {final_requirements_path.absolute()}")
    
    # Pass the timeout arguments directly to the PackageDownloader constructor
    downloader = PackageDownloader(
        requirements_file=final_requirements_path,
        dry_run=args.dry_run,
        concurrency=args.concurrency,
        download_dir=download_dir,
        use_cn_mirrors=args.cn,
        python_version=args.python_version,
        abi=args.abi,
        platform=args.platform,
        all_versions=args.all_versions,
        save_url_list=args.save_url_list,
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

    for result in package_sync_results:
        package = result.get("package", "N/A")
        version = result.get("version", "N/A")
        status = result.get("status", "Unknown")
        details = result.get("details", "")

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
