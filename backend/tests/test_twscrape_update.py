from pathlib import Path

from scripts.update_twscrape import locked_versions


def test_locked_versions_reads_twscrape_and_curl_backend(tmp_path: Path) -> None:
    lock_path = tmp_path / "uv.lock"
    lock_path.write_text(
        """
[[package]]
name = "twscrape"
version = "0.20.0"

[[package]]
name = "curl-cffi"
version = "0.16.1"

[[package]]
name = "httpx"
version = "0.28.1"
""".lstrip(),
        encoding="utf-8",
    )

    assert locked_versions(lock_path) == {"twscrape": "0.20.0", "curl-cffi": "0.16.1"}


def test_locked_versions_rejects_missing_lock_file(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing-uv.lock"

    try:
        locked_versions(missing_path)
    except FileNotFoundError as exc:
        assert str(missing_path) in str(exc)
    else:
        raise AssertionError("missing lock file should be rejected")
