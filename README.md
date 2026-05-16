# PyPI Downloader

A fast, asynchronous Python CLI tool to download packages from PyPI mirrors and serve them as a private offline index.

## Purpose

This tool is designed for building internal PyPI mirrors in air-gapped or restricted network environments.

### Use Case

Your development environment is in an internal network without direct internet access. Your team uses:

- Multiple Python 3 versions (3.8, 3.9, 3.11, etc.)
- Different processor architectures (x86_64, ARM, etc.)
- Various operating systems (Linux, Windows, macOS)

The challenge: when you need a PyPI package, you want to download it once with all its versions, architectures, and dependencies, then deploy to your internal PyPI server so all developers can install what they need.

The solution: this tool resolves dependencies automatically, downloads all Python 3 compatible versions and wheels, and can immediately start a `pypiserver` instance ready for `pip install`.

### Key Benefits

- One-time download: get all versions and platforms in a single run
- Heterogeneous support: works for teams with mixed Python versions and architectures
- Dependency resolution: automatically includes all transitive dependencies via `pip-compile`
- Production-ready: SHA-256 verification with PyPI API hashes, retry logic, and mirror fallback
- Smart caching: verifies existing files and skips re-download if hash matches (100x faster on re-runs)
- Fast: async concurrent downloads (16 streams by default) + thread pool for file I/O
- China-friendly: built-in support for 14 Chinese mirrors
- Mirror-safe: uses pip User-Agent to avoid being blocked by PyPI mirrors

---

## Highlights

- All versions download: download all Python 3 versions of each package with `--all-versions`
- Latest patch mode: download only the latest patch version for each minor version with `--latest-patch` (60-70% fewer files)
- Multi-mirror fallback: retries the next mirror automatically if one fails (14 Chinese mirrors + official PyPI)
- Async and concurrent: hundreds of files in parallel without blocking (default: 16 streams, configurable)
- Hash verification: SHA-256 integrity check using PyPI API hashes for every file
- Smart skip: verifies existing files with hash, skips re-download if valid
- Non-blocking I/O: uses thread pool for file operations, never blocks the event loop
- Automatic dependency resolution: always uses `pip-compile` to resolve all transitive dependencies
- Platform filtering: download only wheels for specific Python version, ABI, or platform
- Dry-run mode: preview URLs before downloading (automatically saves URL list)
- Private PyPI server: start an offline `pypiserver` instance with `--serve` after downloading
- Python 3 only: automatically ignores Python 2 packages

---

## Installation

### From PyPI

```bash
pip install pypi-downloader
```

### With optional pypiserver support (for --serve)

```bash
pip install pypi-downloader[full]
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
usage: pypi-downloader [-h] [-r REQUIREMENT_FILE] [--dry-run] [--concurrency N]
                       [--download-dir DIR] [--cn] [--serve] [--serve-port PORT]
                       [--python-version PYTHON_VERSION] [--abi ABI]
                       [--platform PLATFORM] [--all-versions] [--latest-patch]
                       [--url-list-path PATH]
                       [requirements]

PyPI Package Downloader v0.8.1 - Async downloader for building offline PyPI mirrors.
Dependencies are always resolved automatically via pip-compile (pip-tools required).
Use --serve to start a pypiserver private index after downloading.

positional arguments:
  requirements          Path to requirements.txt file

options:
  -h, --help            show this help message and exit
  -r, --requirement REQUIREMENT_FILE
                        Path to requirements.txt (pip-style)
  --dry-run             Only collect URLs and save to file, do not download
  --concurrency N       Max concurrent downloads (default: 16)
  --download-dir DIR    Folder to save packages (default: ./pypi)
  --cn                  Use Chinese PyPI mirrors with automatic fallback
  --serve               Start a pypiserver private PyPI server from the
                        download directory after downloading
  --serve-port PORT     Port for the pypiserver (default: 8080, only used with --serve)
  --python-version PYTHON_VERSION
                        Filter by Python version tag (e.g., cp311, py3, py2.py3)
  --abi ABI             Filter by ABI tag (e.g., cp311, abi3, none)
  --platform PLATFORM   Filter by platform tag (e.g., manylinux_2_17_x86_64, win_amd64, any)
  --all-versions        Download all available Python 3 versions of each package
  --latest-patch        Download only the latest patch version for each minor version.
                        Mutually exclusive with --all-versions
  --url-list-path PATH  Custom path for URL list file (default: ./url_list.txt,
                        only used in dry-run mode)

Examples:
  pypi-downloader                                  # use ./requirements.txt
  pypi-downloader -r reqs.txt --cn                 # Chinese mirrors
  pypi-downloader -r reqs.txt --all-versions --cn  # all Python 3 versions
  pypi-downloader -r reqs.txt --latest-patch --cn  # latest patch per minor
  pypi-downloader -r reqs.txt --cn --serve         # download then serve
  pypi-downloader -r reqs.txt --dry-run            # preview URLs only
```

Note: dependencies are always resolved automatically using `pip-compile` (requires `pip-tools`).

---

## Advanced Examples

### Download All Versions (Internal PyPI Mirror)

Perfect for building an internal PyPI mirror with all Python 3 versions:

```bash
# Resolve all dependencies, download ALL Python 3 versions, then serve
pypi-downloader -r requirements.txt --all-versions --cn --serve

# What happens:
# 1. pip-compile resolves all transitive dependencies
# 2. Downloads ALL Python 3 compatible versions, for example:
#    numpy: 1.19.0, 1.19.1, ..., 1.26.4 (all versions)
#    pandas: 1.0.0, 1.0.1, ..., 2.2.2 (all versions)
# 3. Starts pypiserver on port 8080 after downloading
```

Use case: your internal network has machines with different Python 3 versions (3.8, 3.9, 3.11) and architectures (x86_64, ARM). This command downloads all wheels so any machine can install what it needs.

### Latest Patch Mode (Optimized Mirror)

Download only the latest patch version for each minor version (60-70% fewer files):

```bash
# Keep 2.1.9 (not 2.1.3, 2.1.5), keep 2.2.8 (not 2.2.2)
pypi-downloader -r requirements.txt --latest-patch --cn --serve

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

### Build Self-Hosted PyPI Mirror

Download packages and start a private PyPI server:

```bash
# Download packages (dependencies are automatically resolved via pip-compile)
pypi-downloader -r requirements.txt \
  --download-dir /var/www/pypi \
  --cn

# Download and immediately start the private PyPI server on port 8080 (default)
pypi-downloader -r requirements.txt \
  --download-dir /var/www/pypi \
  --cn \
  --serve

# Use a custom port
pypi-downloader -r requirements.txt \
  --download-dir /var/www/pypi \
  --cn \
  --serve \
  --serve-port 9090
```

Then install packages from the private server:

```bash
pip install --index-url http://localhost:8080/simple/ numpy
```

### Chinese Mirror Support

Use Chinese mirrors for faster downloads in China:

```bash
pypi-downloader -r requirements.txt --cn
```

Supported mirrors (14 total, randomized at startup):
- Aliyun, Tencent Cloud, Tsinghua, USTC, BFSU, SJTU, NJU, NYIST, PKU, QLU, ZJU, NJTech, JLU, Neusoft
- Official PyPI is always tried last as a fallback

---

## Requirements

- Python 3.11+
- `aiohttp`, `loguru`, `rich`, `pip-tools`, `packaging` (installed automatically)

### Optional Dependencies

- `pypiserver` for `--serve` (offline private PyPI server)

```bash
pip install pypiserver
# or install with the full extras:
pip install pypi-downloader[full]
```

---

## Architecture

The tool uses a two-phase execution model:

1. Metadata phase: fetch package metadata from PyPI API and count total files to download
2. Download phase: download all files concurrently with progress tracking

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
