# PyPI Downloader

A fast, asynchronous Python CLI tool to **bulk-download packages from PyPI mirrors** with automatic fallback, concurrency control, hash verification, and rich terminal output.

---

## ✨ Highlights

- **Multi-mirror fallback** – retries the next mirror automatically if one fails
- **Async & concurrent** – hundreds of files in parallel without blocking
- **Hash verification** – SHA-256 integrity check on every file
- **Dry-run mode** – preview URLs or disk usage before you download
- **Rich terminal UI** – colorful tables and progress logs via [Rich][rich]
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
usage: pypi-downloader [-h] [--dry-run] [--concurrency N] [--download-dir DIR]
                       [requirements]

Async PyPI mirror downloader

positional arguments:
  requirements          Path to requirements.txt (default: requirements.txt)

optional arguments:
  -h, --help            show this help message and exit
  --dry-run             Only collect URLs, do not download
  --concurrency N       Max concurrent downloads (default: 256)
  --download-dir DIR    Folder to save packages (default: ./packages)

```

2025-07-29 12:34:56 | INFO | Packages will be downloaded to: /home/user/packages
2025-07-29 12:34:57 | INFO | Downloaded: numpy-1.26.4-cp311-cp311-manylinux_2_17_x86_64.whl
...

Package Synchronization Summary (bold magenta) |
| Package | Version | Status | Details |
| --- | --- | --- | --- |
| numpy | 1.26.4 | Succeed | All 1 file(s) processed |
| pandas | 2.2.2 | Succeed | All 1 file(s) processed |
| torch | 2.3.0 | Failed | All mirrors failed: 404 Not Found |

## 🔧 Requirements

- Python 3.11+
- aiohttp, loguru, rich (installed automatically)

## 🤝 Contributing

Pull requests are welcome! For major changes, please open an issue first to discuss what you’d like to improve.

---

## 📄 License

MIT © [Hektorwang]
