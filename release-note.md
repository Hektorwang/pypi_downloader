# Release Notes

## v0.2.0 (2026-02-18)

### Project Purpose

This tool is designed for **building internal PyPI mirrors in air-gapped or restricted network environments**. It solves the challenge of downloading Python packages with all their versions, architectures, and dependencies for deployment to internal networks where development machines have:
- Multiple Python 3 versions (3.8, 3.9, 3.11, etc.)
- Different processor architectures (x86_64, ARM, etc.)
- Various operating systems (Linux, Windows, macOS)

The tool enables one-time bulk downloads of all Python 3 compatible versions and wheels, with automatic dependency resolution and pip-compatible index building.

---

### 🎉 Major Features

#### All Versions Download
- Added `--all-versions` flag to download all Python 3 compatible versions of each package
- Automatically filters out Python 2 only versions
- Perfect for building comprehensive internal PyPI mirrors
- Ignores version pins in requirements.txt when enabled
- Example: `pypi-downloader -r requirements.txt --all-versions` downloads numpy 1.19.0 through 1.26.4 (all Python 3 versions)
- Use case: Internal networks with heterogeneous Python 3 environments (different versions and architectures)

#### URL List Export
- Added `--save-url-list` flag to save all download URLs to a file
- Default location: `./url_list.txt` (current directory, not download directory)
- Custom path supported with `--url-list-path`
- Useful for auditing, using with other download tools, or keeping records
- Works with both normal and `--all-versions` modes

#### Chinese Mirror Support
- Added `--cn` flag to use 14 Chinese PyPI mirrors with automatic fallback
- Supported mirrors: Aliyun, Tencent Cloud, Tsinghua, USTC, BFSU, SJTU, NJU, NYIST, PKU, QLU, ZJU, NJTech, JLU, Neusoft
- Automatic mirror rotation on failure
- Default behavior uses official PyPI (https://pypi.org)

#### Dependency Resolution
- Added `--resolve-deps` flag to automatically resolve transitive dependencies
- Uses pip-compile from pip-tools to generate complete dependency graph
- Saves resolved dependencies to `{download_dir}/requirements-resolved.txt`
- Integrates with `--cn` flag to use Chinese mirrors for faster resolution

#### Platform Filtering
- Added `--python-version` to filter by Python implementation and version (e.g., `cp311`, `py3`)
- Added `--abi` to filter by ABI tag (e.g., `cp311`, `abi3`, `none`)
- Added `--platform` to filter by platform tag (e.g., `manylinux_2_17_x86_64`, `win_amd64`, `any`)
- Supports PEP 425 wheel filename format with compressed tags (e.g., `py2.py3`)
- Automatically ignores Python 2 only packages (tool is Python 3 only)

#### PyPI Index Building
- Added `--build-index` flag to automatically build pip-compatible index after downloading
- Uses dir2pi from pip2pi to create standard PyPI simple index structure
- Creates `{download_dir}/simple/` directory with package index
- Compatible with `pip install --index-url=file://...`

#### pip-style Arguments
- Added `-r` / `--requirement` flag for pip-compatible syntax
- Supports both positional argument and `-r` flag
- Defaults to `./requirements.txt` if no file specified

### 🔧 Improvements

- Changed default download directory from `./packages` to `./pypi`
- Enhanced logging with detailed filter information
- Source distributions (`.tar.gz`, `.zip`) always pass through filters (platform-independent)
- Better error messages for missing optional dependencies

### 📦 Dependencies

#### Core Dependencies (required)
- aiohttp >= 3.11
- loguru >= 0.6
- rich >= 12.0

#### Optional Dependencies
- pip-tools >= 7.0.0 (for `--resolve-deps`)
- pip2pi >= 0.8.0 (for `--build-index`)

Install with all optional dependencies:
```bash
pip install pypi-downloader[full]
```

### 📝 Usage Examples

#### Basic Usage
```bash
# Download from requirements.txt using Chinese mirrors
pypi-downloader -r requirements.txt --cn

# Resolve dependencies and download
pypi-downloader -r requirements.txt --resolve-deps --cn
```

#### Internal PyPI Mirror (Recommended Workflow)
```bash
# Complete workflow for internal network deployment
pypi-downloader -r requirements.txt \
  --all-versions \
  --cn \
  --resolve-deps \
  --build-index \
  --save-url-list \
  --download-dir /var/www/pypi

# This will:
# 1. Resolve all dependencies with pip-compile
# 2. Download ALL Python 3 versions of each package
# 3. Build pip-compatible index at /var/www/pypi/simple/
# 4. Save URL list to ./url_list.txt for auditing

# Deploy to internal network and use:
pip install --index-url=file:///var/www/pypi/simple/ numpy
```

#### Platform-Specific Downloads
```bash
# Download only CPython 3.11 wheels for Linux x86_64
pypi-downloader -r requirements.txt \
  --python-version cp311 \
  --platform manylinux_2_17_x86_64

# Download pure Python wheels only
pypi-downloader -r requirements.txt \
  --abi none \
  --platform any
```

#### Build Self-Hosted Mirror
```bash
# Complete workflow: resolve deps, filter, download, build index
pypi-downloader -r requirements.txt \
  --resolve-deps \
  --cn \
  --python-version cp311 \
  --platform manylinux_2_17_x86_64 \
  --build-index \
  --download-dir /var/www/pypi

# Use the mirror
pip install --index-url=file:///var/www/pypi/simple/ numpy
```

### 🐛 Bug Fixes

- Fixed URL rewriting to work with both official PyPI and Chinese mirrors
- Fixed metadata fetching to handle both mirror formats
- Improved error handling for network failures

### 🔄 Breaking Changes

- Default download directory changed from `./packages` to `./pypi`
- Python 2 only packages are now automatically filtered out (tool is Python 3 only)

### 🎯 Design Philosophy

This release focuses on the core use case: **building comprehensive internal PyPI mirrors for air-gapped environments**. The `--all-versions` feature is the centerpiece, enabling teams to download once and serve all developers regardless of their Python version or architecture. Combined with `--resolve-deps` and `--build-index`, it provides a complete solution for internal PyPI deployment.

---

## v0.1.1 (Initial Release)

### Features

- Async concurrent downloads with configurable concurrency (default: 256)
- SHA-256 hash verification for all downloaded files
- Dry-run mode to preview downloads
- Rich terminal UI with colored output and progress tables
- Automatic retry logic (5 attempts per file)
- Timeout handling for network operations
- Support for requirements.txt parsing with extras (e.g., `package[extra]==1.0`)

### Core Functionality

- Direct HTTP downloads from PyPI JSON API
- Downloads all distribution files for specified versions
- Concurrent download management with semaphore control
- Hash verification to avoid re-downloading identical files
- URL list generation for offline use
