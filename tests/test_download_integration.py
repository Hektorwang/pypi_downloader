"""Integration tests for download_file and fetch_metadata against a local aiohttp server."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from pypi_downloader.downloader import PackageDownloader

CONTENT = b"fake-wheel-content-" * 100_000  # ~1.9 MB to exercise chunked streaming
CONTENT_SHA256 = hashlib.sha256(CONTENT).hexdigest()

# Minimal PyPI-style metadata served by the local server for metadata tests.
METADATA_JSON = json.dumps(
    {
        "info": {"name": "six"},
        "releases": {
            "1.17.0": [
                {
                    "filename": "six-1.17.0-py2.py3-none-any.whl",
                    "url": "http://127.0.0.1:1/packages/aa/six-1.17.0-py2.py3-none-any.whl",
                    "digests": {"sha256": CONTENT_SHA256},
                }
            ]
        },
    }
).encode()

RunAsync = Callable[[Callable[[], Awaitable[None]]], None]


def _run(coro_factory: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(coro_factory())


@pytest.fixture()
def run_async() -> RunAsync:
    """Run an async scenario to completion."""
    return _run


async def _start_server(body: bytes | None) -> tuple[web.AppRunner, int, list[str]]:
    """Start a local HTTP server serving ``body`` (or 404 if None).

    Also serves metadata JSON on the mirror-style path ``/pypi/web/json/six``
    and the official-style path ``/six/json``, so metadata-cycle tests run
    fully hermetically.

    Returns:
        Tuple of (runner to clean up, bound port, list of requested paths).
    """
    requests: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        requests.append(request.path)
        if request.path in (
            "/pypi/web/json/six",  # TUNA-style layout
            "/pypi/json/six",  # plain layout
            "/six/json",  # official-style URL
        ):
            return web.Response(body=METADATA_JSON)
        if body is None:
            return web.Response(status=404, text="not found")
        return web.Response(body=body)

    app = web.Application()
    app.router.add_route("GET", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    return runner, port, requests


def _make_downloader(tmp_path: Path) -> PackageDownloader:
    return PackageDownloader(
        requirements_content="", download_dir=tmp_path, concurrency=4
    )


class TestDownloadFile:
    def test_success_with_hash_verification(
        self, tmp_path: Path, run_async: RunAsync
    ) -> None:
        async def scenario() -> None:
            runner, port, _requests = await _start_server(CONTENT)
            try:
                downloader = _make_downloader(tmp_path)
                async with aiohttp.ClientSession() as session:
                    downloader.session = session
                    ok = await downloader.download_file(
                        f"http://127.0.0.1:{port}/packages/aa/bb/pkg-1.0-py3-none-any.whl",
                        "pkg-1.0-py3-none-any.whl",
                        f"sha256={CONTENT_SHA256}",
                    )
                assert ok is True
                dest = tmp_path / "pkg-1.0-py3-none-any.whl"
                assert dest.read_bytes() == CONTENT
                assert not list(tmp_path.glob("*.part")), "no partial file may remain"
            finally:
                await runner.cleanup()

        run_async(scenario)

    def test_existing_valid_file_is_skipped_without_requests(
        self, tmp_path: Path, run_async: RunAsync
    ) -> None:
        async def scenario() -> None:
            dest = tmp_path / "pkg-1.0-py3-none-any.whl"
            dest.write_bytes(CONTENT)
            runner, port, requests = await _start_server(b"different-content")
            try:
                downloader = _make_downloader(tmp_path)
                async with aiohttp.ClientSession() as session:
                    downloader.session = session
                    ok = await downloader.download_file(
                        f"http://127.0.0.1:{port}/pkg-1.0-py3-none-any.whl",
                        "pkg-1.0-py3-none-any.whl",
                        f"sha256={CONTENT_SHA256}",
                    )
                assert ok is True
                assert dest.read_bytes() == CONTENT, "existing file must not be touched"
                assert requests == [], "no HTTP request should be made for a cached file"
            finally:
                await runner.cleanup()

        run_async(scenario)

    def test_existing_file_without_hash_is_trusted(
        self, tmp_path: Path, run_async: RunAsync
    ) -> None:
        async def scenario() -> None:
            dest = tmp_path / "pkg-1.0-py3-none-any.whl"
            dest.write_bytes(CONTENT)
            runner, port, requests = await _start_server(b"x")
            try:
                downloader = _make_downloader(tmp_path)
                async with aiohttp.ClientSession() as session:
                    downloader.session = session
                    ok = await downloader.download_file(
                        f"http://127.0.0.1:{port}/pkg-1.0-py3-none-any.whl",
                        "pkg-1.0-py3-none-any.whl",
                        expected_hash=None,
                    )
                assert ok is True
                assert requests == []
            finally:
                await runner.cleanup()

        run_async(scenario)

    def test_hash_mismatch_exhausts_retries_and_cleans_up(
        self, tmp_path: Path, run_async: RunAsync
    ) -> None:
        async def scenario() -> None:
            wrong = b"corrupted-content"
            runner, port, _requests = await _start_server(wrong)
            try:
                downloader = _make_downloader(tmp_path)
                downloader.DEFAULT_RETRIES = 4  # keep the test fast
                async with aiohttp.ClientSession() as session:
                    downloader.session = session
                    ok = await downloader.download_file(
                        f"http://127.0.0.1:{port}/pkg-1.0-py3-none-any.whl",
                        "pkg-1.0-py3-none-any.whl",
                        f"sha256={CONTENT_SHA256}",
                    )
                assert ok is False
                assert not (tmp_path / "pkg-1.0-py3-none-any.whl").exists()
                assert not list(tmp_path.glob("*.part"))
            finally:
                await runner.cleanup()

        run_async(scenario)

    def test_404_fails_cleanly(self, tmp_path: Path, run_async: RunAsync) -> None:
        async def scenario() -> None:
            runner, port, _requests = await _start_server(None)
            try:
                downloader = _make_downloader(tmp_path)
                downloader.DEFAULT_RETRIES = 2  # keep the test fast
                async with aiohttp.ClientSession() as session:
                    downloader.session = session
                    ok = await downloader.download_file(
                        f"http://127.0.0.1:{port}/pkg-1.0-py3-none-any.whl",
                        "pkg-1.0-py3-none-any.whl",
                        f"sha256={CONTENT_SHA256}",
                    )
                assert ok is False
                assert not (tmp_path / "pkg-1.0-py3-none-any.whl").exists()
                assert not list(tmp_path.glob("*.part"))
            finally:
                await runner.cleanup()

        run_async(scenario)

    def test_download_without_session_raises(self, tmp_path: Path) -> None:
        downloader = _make_downloader(tmp_path)
        with pytest.raises(RuntimeError, match="ClientSession"):
            asyncio.run(
                downloader.download_file(
                    "http://127.0.0.1:1/pkg-1.0-py3-none-any.whl",
                    "pkg-1.0-py3-none-any.whl",
                )
            )


class TestFetchMetadata:
    """The metadata cycle: mirrors first (one attempt each), official last."""

    def test_metadata_served_by_mirror(
        self, tmp_path: Path, run_async: RunAsync, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def scenario() -> None:
            runner, port, requests = await _start_server(None)
            # Patch the official API host too so the whole cycle stays local.
            monkeypatch.setattr(PackageDownloader, "PYPI_JSON_API", f"http://127.0.0.1:{port}")
            try:
                downloader = PackageDownloader(
                    requirements_content="",
                    download_dir=tmp_path,
                    concurrency=4,
                    # A custom mirror exposes the JSON endpoint under /pypi.
                    custom_mirrors=[f"http://127.0.0.1:{port}/pypi"],
                )
                async with aiohttp.ClientSession() as session:
                    downloader.session = session
                    metadata = await downloader.fetch_metadata("six")
                assert metadata is not None
                assert metadata["info"]["name"] == "six"
                # Metadata availability must not move the download anchor.
                assert downloader._preferred_mirror_idx == 0
                # Regression: the URL must be single-slash (the old code
                # produced "web/json//six"); the mirror is tried first.
                assert "/pypi/web/json/six" in requests
                assert "/pypi/web/json//six" not in requests
            finally:
                await runner.cleanup()

        run_async(scenario)

    def test_mirror_404_falls_through_to_official(
        self, tmp_path: Path, run_async: RunAsync, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def scenario() -> None:
            runner, port, requests = await _start_server(None)
            monkeypatch.setattr(PackageDownloader, "PYPI_JSON_API", f"http://127.0.0.1:{port}")
            try:
                downloader = _make_downloader(tmp_path)
                # A custom mirror whose JSON endpoint 404s (the handler above
                # only serves JSON for /pypi/web/json/six).
                downloader._available_mirrors = [
                    f"http://127.0.0.1:{port}/empty",
                    PackageDownloader.OFFICIAL_PYPI,
                ]
                downloader._preferred_mirror_idx = 0
                async with aiohttp.ClientSession() as session:
                    downloader.session = session
                    metadata = await downloader.fetch_metadata("six")
                assert metadata is not None
                assert "/empty/web/json/six" in requests  # mirror tried first
                assert "/six/json" in requests  # then the official-style URL
            finally:
                await runner.cleanup()

        run_async(scenario)

    def test_metadata_plain_layout_path(
        self, tmp_path: Path, run_async: RunAsync, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def scenario() -> None:
            runner, port, requests = await _start_server(None)
            monkeypatch.setattr(PackageDownloader, "PYPI_JSON_API", f"http://127.0.0.1:{port}")
            try:
                downloader = PackageDownloader(
                    requirements_content="",
                    download_dir=tmp_path,
                    concurrency=4,
                    custom_mirrors=[f"http://127.0.0.1:{port}/pypi"],
                )
                # Simulate a plain-layout mirror (like tencent/volces): the
                # JSON endpoint must be derived from the file prefix.
                monkeypatch.setattr(
                    PackageDownloader,
                    "_MIRROR_FILE_PREFIXES",
                    {f"http://127.0.0.1:{port}/pypi": "packages/"},
                )
                async with aiohttp.ClientSession() as session:
                    downloader.session = session
                    metadata = await downloader.fetch_metadata("six")
                assert metadata is not None
                assert "/pypi/json/six" in requests
                assert "/pypi/web/json/six" not in requests
            finally:
                await runner.cleanup()

        run_async(scenario)

    def test_official_404_means_package_not_found(
        self, tmp_path: Path, run_async: RunAsync, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def scenario() -> None:
            runner, port, _requests = await _start_server(None)
            monkeypatch.setattr(PackageDownloader, "PYPI_JSON_API", f"http://127.0.0.1:{port}")
            try:
                downloader = _make_downloader(tmp_path)
                async with aiohttp.ClientSession() as session:
                    downloader.session = session
                    metadata = await downloader.fetch_metadata("does-not-exist")
                # 404 on the official API is authoritative: package missing.
                assert metadata is None
                # The result is cached so a second call stays hermetic.
                again = await downloader.fetch_metadata("does-not-exist")
                assert again is None
            finally:
                await runner.cleanup()

        run_async(scenario)
