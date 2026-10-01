"""Command-line interface for pypi-downloader."""

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

from loguru import logger
from rich.console import Console
from rich.table import Table

from pypi_downloader import __version__
from pypi_downloader.downloader import LocalIOFatalError, PackageDownloader
from pypi_downloader.resolver import DependencyResolver
from pypi_downloader.ui import RichLogSink, configure_logging

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_INTERRUPTED = 130

# Statuses that mean at least one package did not fully synchronize.
_FAILURE_STATUSES = frozenset({"Failed", "Partial Sync", "Error (Pre-filter)"})


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse CLI parser.

    Returns:
        The fully configured argument parser (does not parse sys.argv).
    """
    parser = argparse.ArgumentParser(
        description=(
            f"PyPI Package Downloader v{__version__} - "
            "Async downloader for building offline PyPI mirrors. "
            "Dependencies are always resolved automatically via pip-compile (pip-tools required)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  pypi-downloader                                  # use ./requirements.txt\n"
            "  pypi-downloader -r reqs.txt --cn                 # Chinese mirrors\n"
            "  pypi-downloader -r reqs.txt --all-versions --cn  # all Python 3 versions\n"
            "  pypi-downloader -r reqs.txt --latest-patch --cn  # latest patch per minor\n"
            "  pypi-downloader -r reqs.txt --mirror https://mirror.example.com/pypi\n"
            "  pypi-downloader -r reqs.txt --dry-run            # preview URLs only\n"
        ),
    )
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
        help="Only collect URLs and save to file, do not download",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=PackageDownloader.DEFAULT_CONCURRENCY,
        help="Max concurrent downloads (default: 16)",
    )
    parser.add_argument(
        "--download-dir",
        type=str,
        default=str(Path.cwd() / "pypi"),
        help="Folder to save packages (default: ./pypi)",
    )
    parser.add_argument(
        "--cn",
        action="store_true",
        help="Use Chinese PyPI mirrors with automatic fallback",
    )
    parser.add_argument(
        "--mirror",
        action="append",
        dest="custom_mirrors",
        metavar="URL",
        help=(
            "Custom mirror base URL, tried before the built-in list "
            "(repeatable, e.g. https://mirror.example.com/pypi)"
        ),
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
        help="Download all available Python 3 versions of each package (ignores version pins)",
    )
    parser.add_argument(
        "--latest-patch",
        action="store_true",
        help=(
            "Download only the latest patch version for each minor version "
            "(mutually exclusive with --all-versions)"
        ),
    )
    parser.add_argument(
        "--url-list-path",
        type=str,
        help="Custom path for URL list file (default: ./url_list.txt, dry-run mode only)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def _print_summary(package_sync_results: list[dict]) -> None:
    """Print the package synchronization summary table.

    Each package is shown with its resolved version, final status, and details.
    Status colors: green for "Synchronized", yellow for "Partial Sync", red for
    "Failed" / "Error (Pre-filter)", blue for "No Files".

    Args:
        package_sync_results: Status dicts as returned by
            :meth:`PackageDownloader.run`.
    """
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


def main() -> int:
    """Main entry point for command-line execution.

    Flow: parse arguments, resolve dependencies via pip-compile, download all
    packages, then print the synchronization summary. Logging is configured
    only after argument parsing so that ``--help`` / ``--version`` do not
    create the log file. The Rich live display is always stopped, even when
    the download raises.

    Returns:
        The process exit code:
        - 0: all packages fully synchronized.
        - 1: failure (bad arguments, missing requirements file, resolution
          error, fatal local I/O error, or at least one package with status
          "Failed" / "Partial Sync" / "Error (Pre-filter)").
        - 130: interrupted by the user (Ctrl+C); downloaded files are kept.
    """
    parser = build_parser()
    args = parser.parse_args()

    if args.latest_patch and args.all_versions:
        parser.error("--latest-patch and --all-versions are mutually exclusive")

    # Configure logging only after parsing, so --help/--version do not create
    # the ./pypi-downloader.log file.
    configure_logging(use_rich=False)

    requirements_path = (
        args.requirement_file
        or args.requirements
        or str(Path.cwd() / "requirements.txt")
    )
    if not Path(requirements_path).exists():
        logger.error(f"Requirements file not found: {requirements_path}")
        return EXIT_FAILURE

    download_dir = Path(args.download_dir)
    url_list_path = Path(args.url_list_path) if args.url_list_path else None

    if args.all_versions:
        logger.info(
            "Note: --all-versions is enabled, version pins will be ignored during download"
        )

    resolver = DependencyResolver(
        requirements_path=Path(requirements_path),
        use_cn_mirrors=args.cn,
    )
    try:
        resolved_content: str = resolver.resolve()
    except (FileNotFoundError, subprocess.CalledProcessError, RuntimeError) as exc:
        logger.error(f"Dependency resolution failed: {exc}")
        return EXIT_FAILURE
    except Exception as exc:  # noqa: BLE001 - surface unexpected resolver errors
        logger.error(f"Unexpected error resolving dependencies: {exc}")
        return EXIT_FAILURE

    logger.info(f"Packages will be downloaded to: {download_dir.absolute()}")

    downloader = PackageDownloader(
        requirements_content=resolved_content,
        dry_run=args.dry_run,
        concurrency=args.concurrency,
        download_dir=download_dir,
        use_cn_mirrors=args.cn,
        custom_mirrors=args.custom_mirrors,
        python_version=args.python_version,
        abi=args.abi,
        platform=args.platform,
        all_versions=args.all_versions,
        latest_patch=args.latest_patch,
        url_list_path=url_list_path,
    )

    logger.info("=" * 60)
    logger.info("Switching to live progress display...")
    logger.info("=" * 60)

    rich_sink: RichLogSink | None = configure_logging(use_rich=True)
    downloader.rich_sink = rich_sink

    try:
        package_sync_results = asyncio.run(downloader.run())
    except KeyboardInterrupt:
        logger.warning(
            "Interrupted by user; already-downloaded files are kept and skipped on the next run."
        )
        return EXIT_INTERRUPTED
    except LocalIOFatalError as exc:
        logger.critical(f"Download aborted: {exc}")
        return EXIT_FAILURE
    finally:
        if rich_sink:
            rich_sink.stop()

    _print_summary(package_sync_results)

    failed = any(
        result.get("status") in _FAILURE_STATUSES for result in package_sync_results
    )
    return EXIT_FAILURE if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
