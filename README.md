# PyPI Downloader

A fast, asynchronous Python CLI tool to **download packages from PyPI mirrors**.

## 🎯 Purpose

This tool is designed for **building internal PyPI mirrors in air-gapped or restricted network environments**. 

### Use Case

Your development environment is in an internal network without direct internet access. Your team uses:
- Multiple Python 3 versions (3.8, 3.9, 3.11, etc.)
- Different processor architectures (x86_64, ARM, etc.)
- Various operating systems (Linux, Windows, macOS)

**The Challenge**: When you need a PyPI package, you want to download it once with all its versions, architectures, and dependencies, then deploy to your internal PyPI server so all developers can install what they need.

**The Solution**: This tool downloads all Python 3 compatible versions and wheels of specified packages, resolves dependencies automatically, and builds a pip-compatible index - ready to deploy to your internal network.

### Key Benefits

- ✅ **One-time download**: Get all versions and platforms in a single run
- ✅ **Heterogeneous support**: Works for teams with mixed Python versions and architectures
- ✅ **Dependency resolution**: Automatically includes all transitive dependencies
- ✅ **Production-ready**: SHA-256 verification with PyPI API hashes, retry logic, and mirror fallback
- ✅ **Smart caching**: Verifies existing files and skips re-download if hash matches (100x faster on re-runs)
- ✅ **Fast**: Async concurrent downloads (256 streams) + thread pool for I/O (never blocks event loop)
- ✅ **Optimized**: Chunked hash computation, non-blocking file I/O, efficient memory usage
- ✅ **China-friendly**: Built-in support for 14 Chinese mirrors
- ✅ **Mirror-safe**: Uses pip User-Agent to avoid being blocked by PyPI mirrors

---

## ✨ Highlights

- **All versions download** – download all Python 3 versions of each package with `--all-versions`
- **Multi-mirror fallback** – retries the next mirror automatically if one fails (14 Chinese mirrors + official PyPI)
- **Async & concurrent** – hundreds of files in parallel without blocking (default: 256 streams)
- **Hash verification** – SHA-256 integrity check using PyPI API hashes for every file
- **Smart skip** – verifies existing files with hash, skips re-download if valid (100x faster on re-runs)
- **Non-blocking I/O** – uses thread pool for file operations, never blocks the event loop
- **Automatic dependency resolution** – always uses pip-compile to resolve all transitive dependencies
- **Platform filtering** – download only wheels for specific Python version, ABI, or platform
- **Dry-run mode** – preview URLs before you download (automatically saves URL list)
- **PyPI index builder** – automatically build pip-compatible index with dir2pi
- **Python 3 only** – automatically ignores Python 2 packages
- **Mirror-friendly** – uses pip User-Agent to avoid being blocked

---

## 📦 Installation

### From PyPI (soon)

```bash
pip install pypi-downloader
git clone https://github.com/yourname/pypi-downloader.git
cd pypi-downloader
uv build
pip install dist/*.whl
```

## 🚀 Quick Start

Download every package listed in the current folder’s requirements.txt:

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

## 🛠 Usage

```text
usage: pypi-downloader [-h] [-r REQUIREMENT_FILE] [--dry-run] [--concurrency N]
                       [--download-dir DIR] [--cn] [--build-index]
                       [--python-version PYTHON_VERSION] [--abi ABI]
                       [--platform PLATFORM] [--all-versions]
                       [--url-list-path PATH]
                       [requirements]

Async PyPI mirror downloader

positional arguments:
  requirements          Path to requirements.txt file

options:
  -h, --help            show this help message and exit
  -r, --requirement REQUIREMENT_FILE
                        Path to requirements.txt (pip-style)
  --dry-run             Only collect URLs and save to file, do not download
  --concurrency N       Max concurrent downloads (default: 256)
  --download-dir DIR    Folder to save packages (default: ./pypi)
  --cn                  Use Chinese PyPI mirrors with automatic fallback
  --build-index         Build PyPI-compatible index using dir2pi after downloading
  --python-version PYTHON_VERSION
                        Filter by Python version tag (e.g., cp311, py3, py2.py3)
  --abi ABI             Filter by ABI tag (e.g., cp311, abi3, none)
  --platform PLATFORM   Filter by platform tag (e.g., manylinux_2_17_x86_64, win_amd64, any)
  --all-versions        Download all available Python 3 versions of each package
  --url-list-path PATH  Custom path for URL list file (default: ./url_list.txt, only used in dry-run mode)
```

Note: Dependencies are always resolved automatically using pip-compile (requires pip-tools).
  --url-list-path PATH  Custom path for URL list file
```

2025-07-29 12:34:56 | INFO | Packages will be downloaded to: /home/user/pypi
2025-07-29 12:34:57 | INFO | Downloaded: numpy-1.26.4-cp311-cp311-manylinux_2_17_x86_64.whl
...

Package Synchronization Summary (bold magenta) |
| Package | Version | Status | Details |
| --- | --- | --- | --- |
| numpy | 1.26.4 | Synchronized | All 1 file(s) processed |
| pandas | 2.2.2 | Synchronized | All 1 file(s) processed |
| torch | 2.3.0 | Failed | All mirrors failed: 404 Not Found |

## 📋 Advanced Examples

### Download All Versions (Internal PyPI Mirror)

Perfect for building an internal PyPI mirror with all Python 3 versions:

```bash
# Download all versions of packages listed in requirements.txt
# Dependencies are automatically resolved with pip-compile
pypi-downloader -r requirements.txt --all-versions --cn --build-index

# This will:
# 1. Resolve all dependencies using pip-compile
# 2. Download ALL Python 3 compatible versions, for example:
#    numpy: 1.19.0, 1.19.1, ..., 1.26.4 (all versions)
#    pandas: 1.0.0, 1.0.1, ..., 2.2.2 (all versions)
```

Use case: Your internal network has machines with different Python 3 versions (3.8, 3.9, 3.11) and architectures (x86_64, ARM). This command downloads all wheels so any machine can install what it needs.

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

Download packages and build a pip-compatible index:

```bash
# Dependencies are automatically resolved
pypi-downloader -r requirements.txt \
  --download-dir /var/www/pypi \
  --cn \
  --build-index
```

Then use it with pip:

```bash
pip install --index-url=file:///var/www/pypi/simple/ numpy
```

### Chinese Mirror Support

Use Chinese mirrors for faster downloads in China:

```bash
# Dependencies are automatically resolved using the Chinese mirror
pypi-downloader -r requirements.txt --cn
```

Supported mirrors:
- Aliyun, Tencent Cloud, Tsinghua, USTC, BFSU, SJTU, NJU, and more
- Automatic fallback if one mirror fails

## 🔧 Requirements

- Python 3.11+
- aiohttp, loguru, rich (installed automatically)
- pip-tools (required for automatic dependency resolution)

### Optional Dependencies

- **pip2pi** – for `--build-index` (PyPI index building)
  ```bash
  pip install pip2pi
  ```

## 🤝 Contributing

Pull requests are welcome! For major changes, please open an issue first to discuss what you’d like to improve.

---

## 📄 License

MIT © [Hektorwang]
