"""Rich-based terminal UI: live log sink and logging configuration.

This module owns everything that touches the terminal:

- :class:`RichLogSink`: a Loguru sink that keeps the last N log lines on
  screen above a live Rich progress bar (instead of scrolling infinitely).
- :func:`configure_logging`: wires up console output and the trace-level
  rotating file log (``./pypi-downloader.log``).
"""

import re
import sys
from collections import deque

from loguru import logger
from rich.console import Console, Group
from rich.live import Live
from rich.progress import BarColumn, Progress, TaskID, TextColumn
from rich.text import Text

# Maximum width of a single log line shown in the live display. Longer lines
# are truncated so the display stays readable on narrow terminals.
MAX_LOG_LINE_LENGTH = 120

# Matches "scheme://host/path..." — the scheme is kept in the truncated output
# so users can still tell HTTP vs HTTPS mirrors apart at a glance.
_URL_PATTERN = re.compile(r"(?P<url>https?://[^/\s]+)(?P<path>/\S*)")


def truncate_log_message(message: str, max_length: int = MAX_LOG_LINE_LENGTH) -> str:
    """Shorten a long log line while keeping the mirror host and filename visible.

    Long download URLs are collapsed to ``<prefix><scheme>://<host>...<filename>``
    so the operator can still tell which mirror serves which file. Messages
    without a URL, and URL lines that are still too long after collapsing, are
    hard-truncated with an ellipsis.

    Args:
        message: The raw log line (trailing whitespace is kept as-is).
        max_length: Maximum allowed length of the returned line.

    Returns:
        A line guaranteed not to exceed ``max_length`` characters.
    """
    if len(message) <= max_length:
        return message

    match = _URL_PATTERN.search(message)
    if not match:
        # No URL to summarize: keep the beginning of the line.
        return message[: max_length - 3] + "..."

    prefix = message[: match.start()]
    url = match.group("url")  # scheme + host, e.g. "https://mirrors.aliyun.com"
    # The filename is the last path segment; it identifies the package file.
    filename = match.group("path").rstrip("/").split("/")[-1]
    shortened = f"{prefix}{url}...{filename}"
    if len(shortened) <= max_length:
        return shortened

    # Even "host...filename" is too long: clip the filename in the middle.
    # Reserve 3 + 3 characters for the two ellipses.
    available = max_length - len(prefix) - len(url) - 6
    if available > 10:
        return f"{prefix}{url}...{filename[:available]}..."
    return f"{prefix}{url}..."


class RichLogSink:
    """Loguru sink showing the last N log lines above a Rich progress bar.

    The sink holds a bounded deque of recent messages and re-renders them
    together with the progress bar inside a single Rich ``Live`` region, so
    the terminal shows a stable "log tail + progress" view instead of an
    endlessly scrolling transcript.

    Attributes:
        max_lines: Maximum number of log lines kept on screen.
        lines: Bounded deque of (already truncated) log lines.
        console: Rich console the live display renders to (stderr).
        live: The active ``Live`` instance, or None when stopped.
        progress: The Rich progress bar shown under the log lines.
        task_id: Progress bar task handle, or None before init_progress().
    """

    def __init__(self, max_lines: int = 25) -> None:
        """Create the sink.

        Args:
            max_lines: Maximum number of log lines kept on screen.
        """
        self.max_lines = max_lines
        self.lines: deque[str] = deque(maxlen=max_lines)
        self.console = Console(file=sys.stderr)
        self.live: Live | None = None
        self.progress = Progress(
            TextColumn("[cyan]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("{task.completed}/{task.total} files"),
        )
        self.task_id: TaskID | None = None

    def start(self) -> None:
        """Start the live display (idempotent: does nothing if already running)."""
        if not self.live:
            # vertical_overflow="visible" lets content start from the bottom.
            self.live = Live(
                self._render(),
                console=self.console,
                refresh_per_second=10,
                vertical_overflow="visible",
            )
            self.live.start()

    def write(self, message: str) -> None:
        """Loguru sink protocol: append one message and refresh the display.

        Args:
            message: The formatted log line emitted by Loguru.
        """
        self.lines.append(truncate_log_message(message.rstrip()))
        self._update_display()

    def init_progress(self, total: int) -> None:
        """Create the progress bar with the given total file count.

        Args:
            total: Total number of files that will be processed.
        """
        self.task_id = self.progress.add_task("Downloading", total=total)
        self._update_display()

    def update_progress(self, advance: int = 1) -> None:
        """Advance the progress bar.

        Args:
            advance: Number of completed files to add.
        """
        if self.task_id is not None:
            self.progress.update(self.task_id, advance=advance)
            self._update_display()

    def _render(self) -> Group | Text:
        """Build the current display: log tail, separator, progress bar."""
        log_text = "\n".join(self.lines)
        if self.task_id is not None:
            separator = "\u2500" * 80
            return Group(Text(log_text), Text(separator, style="dim"), self.progress)
        return Text(log_text)

    def _update_display(self) -> None:
        """Push a freshly rendered frame into the live region."""
        if self.live:
            self.live.update(self._render())

    def flush(self) -> None:
        """Loguru sink protocol; Rich refreshes itself on a timer."""

    def stop(self) -> None:
        """Stop the live display (idempotent) and release the terminal."""
        if self.live:
            self.live.stop()
            self.live = None


def configure_logging(use_rich: bool = False) -> RichLogSink | None:
    """Configure Loguru handlers for console and file output.

    Two sinks are installed:

    - Console: either a :class:`RichLogSink` live display (DEBUG+), or a plain
      colored stderr sink (INFO+) for the pre-download phase.
    - File: ``./pypi-downloader.log`` at TRACE level with 10 MB rotation and
      3 retained files, capturing everything for post-mortem analysis.

    Args:
        use_rich: If True, use the Rich live display for console output.

    Returns:
        The created :class:`RichLogSink`, or None when ``use_rich`` is False.
    """
    logger.remove()

    rich_sink: RichLogSink | None = None
    if use_rich:
        rich_sink = RichLogSink(max_lines=20)
        rich_sink.start()
        logger.add(
            rich_sink,
            level="DEBUG",
            format="{time:HH:mm:ss} | {level: <8} | {message}",
        )
    else:
        logger.add(
            sys.stderr,
            level="INFO",
            format=(
                "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
                "<level>{message}</level>"
            ),
            colorize=True,
        )

    # File sink captures everything (TRACE+) for post-mortem analysis.
    logger.add(
        "./pypi-downloader.log",
        level="TRACE",
        rotation="10 MB",
        retention=3,
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    )

    return rich_sink
