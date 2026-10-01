"""Unit tests for the UI helpers (log truncation)."""

from pypi_downloader.ui import truncate_log_message


class TestTruncateLogMessage:
    def test_short_message_unchanged(self) -> None:
        assert truncate_log_message("short message") == "short message"

    def test_exact_length_unchanged(self) -> None:
        message = "x" * 120
        assert truncate_log_message(message) == message

    def test_long_plain_message_truncated(self) -> None:
        message = "y" * 200
        result = truncate_log_message(message)
        assert len(result) == 120
        assert result.endswith("...")

    def test_long_url_keeps_host_and_filename(self) -> None:
        message = (
            "Downloading: https://mirrors.aliyun.com/pypi/web/packages/"
            "aa/bb/cc/some-very-long-package-name-with-many-words-1.0.0-py3-none-any.whl"
        )
        assert len(message) > 120
        result = truncate_log_message(message)
        assert len(result) <= 120
        assert "https://mirrors.aliyun.com" in result
        assert "some-very-long-package-name-with-many-words-1.0.0-py3-none-any.whl" in result

    def test_oversized_filename_is_clipped(self) -> None:
        message = "Downloading: https://mirrors.aliyun.com/pypi/" + "z" * 200 + ".whl"
        result = truncate_log_message(message)
        assert len(result) <= 120
        assert result.startswith("Downloading: https://mirrors.aliyun.com...")
        assert result.endswith("...")

    def test_no_url_falls_back_to_simple_truncation(self) -> None:
        message = "a" * 50 + " " + "b" * 100
        result = truncate_log_message(message)
        assert len(result) == 120
        assert result.endswith("...")
