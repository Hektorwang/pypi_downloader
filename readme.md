# PyPI Downloader

A fast, asynchronous Python CLI tool to **bulk-download packages from PyPI mirrors** with automatic fallback, concurrency control, hash verification, and rich terminal output.

---

## ✨ Highlights

- **Multi-mirror fallback** – retries the next mirror automatically if one fails (14 Chinese mirrors + official PyPI)
- **Async & concurrent** – hundreds of files in parallel without blocking (default: 256 streams)
- **Hash verification** – SHA-256 integrity check on every file
- **Dependency resolution** – uses pip-compile to resolve all transitive dependencies
- **Platform filtering** – download only wheels for specific Python version, ABI, or platform
- **Dry-run mode** – preview URLs or disk usage before you download
- **Rich terminal UI** – colorful tables and progress logs via [Rich][rich]
- **PyPI index builder** – automatically build pip-compatible index with dir2pi
- **Zero-config** – point it at a `requirements.txt` and run

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
                       [--platform PLATFORM] [--resolve-deps]
                       [requirements]

Async PyPI mirror downloader

positional arguments:
  requirements          Path to requirements.txt file

options:
  -h, --help            show this help message and exit
  -r, --requirement REQUIREMENT_FILE
                        Path to requirements.txt (pip-style)
  --dry-run             Only collect URLs, do not download
  --concurrency N       Max concurrent downloads (default: 256)
  --download-dir DIR    Folder to save packages (default: ./pypi)
  --cn                  Use Chinese PyPI mirrors with automatic fallback
  --build-index         Build PyPI-compatible index using dir2pi after downloading
  --python-version PYTHON_VERSION
                        Filter by Python version tag (e.g., cp311, py3, py2.py3)
  --abi ABI             Filter by ABI tag (e.g., cp311, abi3, none)
  --platform PLATFORM   Filter by platform tag (e.g., manylinux_2_17_x86_64, win_amd64, any)
  --resolve-deps        Use pip-compile to resolve dependencies before downloading
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

### Resolve Dependencies and Download

Automatically resolve all transitive dependencies using pip-compile:

```bash
pypi-downloader -r requirements.txt --resolve-deps --cn
```

This will:
1. Run `pip-compile` to resolve all dependencies
2. Save resolved dependencies to `pypi/requirements-resolved.txt`
3. Download all packages including sub-dependencies

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
pypi-downloader -r requirements.txt \
  --download-dir /var/www/pypi \
  --cn \
  --resolve-deps \
  --build-index
```

Then use it with pip:

```bash
pip install --index-url=file:///var/www/pypi/simple/ numpy
```

### Chinese Mirror Support

Use Chinese mirrors for faster downloads in China:

```bash
pypi-downloader -r requirements.txt --cn
```

Supported mirrors:
- Aliyun, Tencent Cloud, Tsinghua, USTC, BFSU, SJTU, NJU, and more
- Automatic fallback if one mirror fails

## 🔧 Requirements

- Python 3.11+
- aiohttp, loguru, rich (installed automatically)

### Optional Dependencies

- **pip-tools** – for `--resolve-deps` (dependency resolution)
  ```bash
  pip install pip-tools
  ```
- **pip2pi** – for `--build-index` (PyPI index building)
  ```bash
  pip install pip2pi
  ```

## 🤝 Contributing

Pull requests are welcome! For major changes, please open an issue first to discuss what you’d like to improve.

---

## 📄 License

MIT © [Hektorwang]
