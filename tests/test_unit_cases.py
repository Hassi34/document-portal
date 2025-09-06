import re
import sys
from pathlib import Path

import pytest

# Ensure the project 'src' directory is importable when running tests without installation
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Lightweight stubs so tests don't require full runtime deps
import types

if "yaml" not in sys.modules:
    sys.modules["yaml"] = types.ModuleType("yaml")
    # Provide a minimal safe_load returning defaults used by get_supported_extensions
    setattr(
        sys.modules["yaml"],
        "safe_load",
        lambda _file: {"data": {"supported_extensions": [".pdf", ".docx", ".txt"]}},
    )

if "src.utils.logger" not in sys.modules:
    logger_stub = types.ModuleType("src.utils.logger")

    class _DummyLogger:
        def info(self, *_, **__):
            pass

        def warning(self, *_, **__):
            pass

        def error(self, *_, **__):
            pass

    logger_stub.GLOBAL_LOGGER = _DummyLogger()
    sys.modules["src.utils.logger"] = logger_stub

import src.utils.config_loader as config_loader
from src.utils import file_io


def test_supported_extensions_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    """Normalize extensions to lowercase with leading dot and strip whitespace."""
    monkeypatch.setattr(
        config_loader,
        "load_config",
        lambda: {"data": {"supported_extensions": ["pdf", ".DOCX", " .txt "]}},
    )

    exts = config_loader.get_supported_extensions()
    assert exts == {".pdf", ".docx", ".txt"}
    print("SUCCESS: test_supported_extensions_normalization")


def test_generate_session_id_format() -> None:
    """Session ID has prefix, sortable timestamp, and 8-hex suffix."""
    sid = file_io.generate_session_id("session")
    # e.g. session_20250101_120102_deadbeef
    assert sid.startswith("session_")
    assert re.match(r"^session_\d{8}_\d{6}_[0-9a-f]{8}$", sid)
    print("SUCCESS: test_generate_session_id_format")


def test_save_uploaded_files_filters_and_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saves only allowed extensions and writes the correct bytes to disk."""
    # Restrict allowed extensions for this test
    monkeypatch.setattr(file_io, "SUPPORTED_EXTENSIONS", {".pdf"}, raising=False)

    class DummyUpload:
        def __init__(self, name: str, data: bytes):
            self.name = name
            self._data = data

        def read(self) -> bytes:
            return self._data

    ok = DummyUpload("report.pdf", b"hello")
    skip = DummyUpload("malware.exe", b"nope")

    saved = file_io.save_uploaded_files([ok, skip], tmp_path)
    assert len(saved) == 1
    p = saved[0]
    assert p.suffix == ".pdf"
    assert p.exists()
    assert p.read_bytes() == b"hello"
    print("SUCCESS: test_save_uploaded_files_filters_and_writes")


def test_generate_session_id_uniqueness() -> None:
    s1 = file_io.generate_session_id("session")
    s2 = file_io.generate_session_id("session")
    assert s1 != s2
    assert re.match(r"^session_\d{8}_\d{6}_[0-9a-f]{8}$", s1)
    assert re.match(r"^session_\d{8}_\d{6}_[0-9a-f]{8}$", s2)
    print("SUCCESS: test_generate_session_id_uniqueness")


def test_save_uploaded_files_uses_getbuffer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(file_io, "SUPPORTED_EXTENSIONS", {".pdf"}, raising=False)

    class DummyBufferUpload:
        def __init__(self, name: str, data: bytes):
            self.name = name
            self._data = data

        def getbuffer(self) -> bytes:
            return self._data

    buf = DummyBufferUpload("buffered.pdf", b"buffer-data")
    saved = file_io.save_uploaded_files([buf], tmp_path)
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"buffer-data"
    print("SUCCESS: test_save_uploaded_files_uses_getbuffer")


def test_save_uploaded_files_skips_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(file_io, "SUPPORTED_EXTENSIONS", {".txt"}, raising=False)

    class DummyUpload:
        def __init__(self, name: str, data: bytes):
            self.name = name
            self._data = data

        def read(self) -> bytes:
            return self._data

    pdf = DummyUpload("notes.pdf", b"pdf")
    saved = file_io.save_uploaded_files([pdf], tmp_path)
    assert saved == []
    print("SUCCESS: test_save_uploaded_files_skips_unsupported")


def test_get_supported_extensions_default_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config_loader, "load_config", lambda: {}, raising=False)
    exts = config_loader.get_supported_extensions()
    assert exts == {".pdf", ".docx", ".txt"}
    print("SUCCESS: test_get_supported_extensions_default_when_missing")
