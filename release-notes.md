# Release Notes

## v0.2.0 (2026-02-18)

### 🎉 Major Features

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
- Python 2 only packages are now automatically filtered out

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
