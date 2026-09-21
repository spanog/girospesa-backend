from __future__ import annotations

from unittest.mock import MagicMock, patch

from services.runtime_memory import current_rss_mib, release_native_memory


def test_current_rss_reads_linux_process_memory() -> None:
    with (
        patch("services.runtime_memory.Path.read_text", return_value="10 100 0"),
        patch("services.runtime_memory.os.sysconf", return_value=4_096),
    ):
        assert current_rss_mib() == 0.4


def test_current_rss_is_absent_without_procfs() -> None:
    with patch("services.runtime_memory.Path.read_text", side_effect=FileNotFoundError):
        assert current_rss_mib() is None


def test_release_native_memory_collects_and_trims_on_linux() -> None:
    libc = MagicMock()
    with (
        patch("services.runtime_memory.gc.collect") as collect,
        patch("services.runtime_memory.sys.platform", "linux"),
        patch("services.runtime_memory.ctypes.CDLL", return_value=libc),
    ):
        release_native_memory()

    collect.assert_called_once_with()
    libc.malloc_trim.assert_called_once_with(0)
