# PyPI Downloader

A fast, asynchronous Python CLI tool to download packages from PyPI mirrors for building offline package sets.

## Purpose

This tool is designed for building internal PyPI mirrors in air-gapped or restricted network environments.

### Use Case

Your development environment is in an internal network without direct internet access. Your team uses:

- Multiple Python 3 versions (3.8, 3.9, 3.11, etc.)
- Different processor architectures (x86_64, ARM, etc.)
- Various operating systems (Linux, Windows, macOS)

The challenge: when you need a PyPI package, you want to download it once with all its versions, architectures, and dependencies, then deploy to your internal PyPI server so all developers can install what they need.

The solution: this tool resolves dependencies automatically, downloads all Python 3 compatible versions and wheels into a single directory that any static file server or PyPI index server can serve.

### Key Benefits

- One-time download: get all versions and platforms in a single run
- Heterogeneous support: works for teams with mixed Python versions and architectures
- Dependency resolution: automatically includes all transitive dependencies via uv (universal resolution covers Windows / macOS / Linux in one pin)
- Production-ready: SHA-256 verification with PyPI API hashes, retry logic, and mirror fallback
- Smart caching: verifies existing files and skips re-download if hash matches (100x faster on re-runs)
- Memory-safe streaming: files stream to disk in 1 MiB chunks and are renamed atomically (GB-sized wheels never load into memory)
- Fast: async concurrent downloads (16 streams by default) + thread pool for file I/O
- China-friendly: built-in support for 4 Chinese mirror sources (Aliyun, Tencent Cloud, Volcengine, CERNET)
- Mirror-safe: uses pip User-Agent to avoid being blocked by PyPI mirrors

---

## Highlights

- All versions download: download all Python 3 versions of each package with `--all-versions`
- Latest patch mode: download only the latest patch version for each minor version with `--latest-patch` (60-70% fewer files)
- Multi-mirror fallback: retries the next mirror automatically if one fails (4 Chinese mirror sources + official PyPI)
- Custom mirrors: bring your own mirror with the repeatable `--mirror` option (tried before the built-in list)
- Async and concurrent: hundreds of files in parallel without blocking (default: 16 streams, configurable)
- Hash verification: SHA-256 integrity check using PyPI API hashes for every file
- Smart skip: verifies existing files with hash, skips re-download if valid
- Non-blocking I/O: uses thread pool for file operations, never blocks the event loop
- Automatic dependency resolution: always uses uv (universal mode) to resolve all transitive dependencies
- Platform filtering: download only wheels for specific Python version, ABI, or platform
- Dry-run mode: preview URLs before downloading (saves the canonical PyPI URL list)
- Python 3 only: automatically ignores Python 2 packages

---

## Installation

### From PyPI

```bash
pip install pypi-downloader
```

### From source

```bash
git clone https://github.com/Hektorwang/pypi_downloader.git
cd pypi-downloader
uv build
pip install dist/*.whl
```

---

## Quick Start

Download every package listed in the current folder's `requirements.txt`:

```bash
pypi-downloader
```

Download to a custom folder, 64 concurrent streams, no actual download (dry-run):

```bash
pypi-downloader requirements.txt \
  --download-dir ./my_mirror \
  --concurrency 64 \
  --dry-run
```

---

## Usage

```text
usage: pypi-downloader [-h] [-r REQUIREMENT_FILE] [--dry-run]
                       [--concurrency CONCURRENCY] [--download-dir DOWNLOAD_DIR]
                       [--cn] [--mirror URL] [--python-version PYTHON_VERSION]
                       [--abi ABI] [--platform PLATFORM] [--all-versions]
                       [--latest-patch] [--url-list-path URL_LIST_PATH]
                       [--version]
                       [requirements]

PyPI Package Downloader v0.10.1 - Async downloader for building offline PyPI mirrors. Dependencies are always resolved automatically via uv (universal mode: one pin covering Windows / macOS / Linux).

positional arguments:
  requirements          Path to the requirements.txt file

options:
  -h, --help            show this help message and exit
  -r, --requirement REQUIREMENT_FILE
                        Path to the requirements.txt file (alternative to
                        positional argument)
  --dry-run             Only collect URLs and save to file, do not download
  --concurrency CONCURRENCY
                        Max concurrent downloads (default: 16)
  --download-dir DOWNLOAD_DIR
                        Folder to save packages (default: ./pypi)
  --cn                  Use Chinese PyPI mirrors with automatic fallback
  --mirror URL          Custom mirror base URL, tried before the built-in list
                        (repeatable, e.g. https://mirror.example.com/pypi)
  --python-version PYTHON_VERSION
                        Filter by Python version tag (e.g., cp311, py3, py2.py3)
  --abi ABI             Filter by ABI tag (e.g., cp311, abi3, none)
  --platform PLATFORM   Filter by platform tag (e.g., manylinux_2_17_x86_64,
                        win_amd64, any)
  --all-versions        Download all available Python 3 versions of each
                        package (ignores version pins)
  --latest-patch        Download only the latest patch version for each minor
                        version (mutually exclusive with --all-versions)
  --url-list-path URL_LIST_PATH
                        Custom path for URL list file (default: ./url_list.txt,
                        dry-run mode only)
  --version             show program's version number and exit

Examples:
  pypi-downloader                                  # use ./requirements.txt
  pypi-downloader -r reqs.txt --cn                 # Chinese mirrors
  pypi-downloader -r reqs.txt --all-versions --cn  # all Python 3 versions
  pypi-downloader -r reqs.txt --latest-patch --cn  # latest patch per minor
  pypi-downloader -r reqs.txt --mirror https://mirror.example.com/pypi
  pypi-downloader -r reqs.txt --dry-run            # preview URLs only
```

Note: dependencies are always resolved automatically using uv in universal mode (one pin covering Windows / macOS / Linux).

---

## Advanced Examples

### Download All Versions (Internal PyPI Mirror)

Perfect for building an internal PyPI mirror with all Python 3 versions:

```bash
# Resolve all dependencies, download ALL Python 3 versions
pypi-downloader -r requirements.txt --all-versions --cn

# What happens:
# 1. pip-compile resolves all transitive dependencies
# 2. Downloads ALL Python 3 compatible versions, for example:
#    numpy: 1.19.0, 1.19.1, ..., 1.26.4 (all versions)
#    pandas: 1.0.0, 1.0.1, ..., 2.2.2 (all versions)
# 3. Packages land in ./pypi, ready for your internal index
```

Use case: your internal network has machines with different Python 3 versions (3.8, 3.9, 3.11) and architectures (x86_64, ARM). This command downloads all wheels so any machine can install what it needs.

### Latest Patch Mode (Optimized Mirror)

Download only the latest patch version for each minor version (60-70% fewer files):

```bash
# Keep 2.1.9 (not 2.1.3, 2.1.5), keep 2.2.8 (not 2.2.2)
pypi-downloader -r requirements.txt --latest-patch --cn

# Example reduction:
# --all-versions: numpy 1.19.0, 1.19.1, 1.19.2, ..., 1.26.4 (100+ versions)
# --latest-patch: numpy 1.19.5, 1.20.3, 1.21.6, 1.22.4, ..., 1.26.4 (~20 versions)
```

Benefits:
- 60-70% fewer files to download
- Faster downloads and less storage
- Still maintains compatibility (patch versions should be backward compatible)

Note: `--latest-patch` and `--all-versions` are mutually exclusive.

### Dry-Run Mode (Preview URLs)

Preview what will be downloaded and save URL list without actually downloading:

```bash
# Dry-run mode automatically saves URLs to ./url_list.txt
pypi-downloader -r requirements.txt --dry-run --cn

# Save to custom location
pypi-downloader -r requirements.txt --dry-run --url-list-path /path/to/urls.txt
```

The saved list contains the canonical `files.pythonhosted.org` URLs — deterministic across runs and directly usable with other download tools (wget, aria2c).

Use cases:
- Audit what will be downloaded before actual download
- Use with other download tools (wget, aria2c)
- Keep a record of package URLs

### Platform-Specific Downloads

Download only wheels compatible with specific platforms:

```bash
# Linux x86_64 with CPython 3.11
pypi-downloader -r requirements.txt \
  --python-version cp311 \
  --abi cp311 \
  --platform manylinux_2_17_x86_64

# Windows AMD64 with CPython 3.11
pypi-downloader -r requirements.txt \
  --python-version cp311 \
  --platform win_amd64

# Pure Python wheels (any platform)
pypi-downloader -r requirements.txt \
  --abi none \
  --platform any
```

### Custom Download Directory and Mirrors

Download to a specific directory, optionally through your own mirror:

```bash
# Download packages to /var/www/pypi (dependencies resolved automatically)
pypi-downloader -r requirements.txt \
  --download-dir /var/www/pypi \
  --cn

# Try a corporate mirror first, then fall back to the built-in list
pypi-downloader -r requirements.txt \
  --download-dir /var/www/pypi \
  --mirror https://mirror.example.com/pypi
```

Point any static file server or PyPI index server at the download directory and install packages from it:

```bash
pip install --index-url http://localhost:8080/simple/ numpy
```

### Chinese Mirror Support

Use Chinese mirrors for faster downloads in China:

```bash
pypi-downloader -r requirements.txt --cn
```

Supported mirror sources (4 total, randomized at startup; official PyPI is always tried last as a fallback):
- Aliyun, Tencent Cloud, CERNET (education network joint mirror), Volcengine
- The CERNET source is a MirrorZ-based aggregator that auto-redirects to the participating university mirror (Tsinghua TUNA, USTC, SJTU, ...) closest to your network
- Each mirror's file layout (with or without the `web/` path prefix) is handled automatically
- Add your own mirror with `--mirror URL` (repeatable, tried before the built-in list)

### Known Limitations

- **Universal resolution strictness**: dependencies are resolved with `uv pip compile --universal`, producing one pin that covers Windows / macOS / Linux (platform-specific dependencies are emitted with environment markers and downloaded too — resolving `tqdm` on Linux pins `colorama` with `sys_platform == 'win32'`). Universal resolution is stricter than per-platform resolution; for the rare dependency graph it cannot satisfy, run the tool once per target platform instead (each run skips files already downloaded and verified).
- **Metadata source**: with `--cn`, package metadata (versions, hashes) is fetched from the Chinese mirrors first — most of them proxy the PyPI JSON API (the endpoint path follows each mirror's file layout; verified for Aliyun, Tencent Cloud, Volcengine, CERNET and the university mirrors behind it). Mirrors without the endpoint are skipped automatically. The official PyPI JSON API remains the final authority and fallback, so "package not found" is still detected correctly and networks where only the mirrors are reachable keep working.

---

## Requirements

- Python 3.11+
- `aiohttp`, `loguru`, `rich`, `uv`, `packaging` (installed automatically)
- `pypiserver` or any static file server if you want to serve the download directory as an index (external, not required by this tool)

---

## Architecture

The tool uses a two-phase execution model:

1. Metadata phase: fetch package metadata concurrently, mirror-first (mirrors proxying the PyPI JSON API serve it directly; the official API is the final authority and fallback, cached; a 404 from the official API fails fast as "package not found") and count total files to download
2. Download phase: download all files concurrently; each attempt rewrites the canonical URL onto the mirror being tried, so switching mirrors always changes the URL actually fetched

Downloads are robust by construction:

- Content streams to a `.part` file in 1 MiB chunks (hash computed on the fly) and is renamed into place atomically after the SHA-256 check passes — interrupted downloads never leave half-written files behind
- Files that already exist with a matching hash are skipped (idempotent re-runs)
- Mirror switching advances a shared preferred-mirror pointer, so later files start from a mirror after one that failed

Internally it uses a hybrid async/threaded architecture:

- asyncio for network I/O (16 concurrent downloads by default)
- ThreadPoolExecutor for file I/O and hash computation (CPU_COUNT * 4 threads, max 32)

This combination maximizes throughput for I/O-bound workloads while keeping the event loop unblocked.

---

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to improve.

---

## License

MIT (c) Hektorwang
