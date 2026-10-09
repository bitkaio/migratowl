# SPDX-License-Identifier: Apache-2.0

"""App logging: migratowl.* INFO lines must reach the server log under uvicorn."""

import logging

import pytest

from migratowl.config import Settings


@pytest.fixture(autouse=True)
def reset_migratowl_logger():
    logger = logging.getLogger("migratowl")
    saved = (logger.level, list(logger.handlers), logger.propagate)
    yield
    logger.setLevel(saved[0])
    logger.handlers[:] = saved[1]
    logger.propagate = saved[2]


def test_default_log_level_is_info() -> None:
    assert Settings(_env_file=None).log_level == "INFO"


def test_configure_logging_emits_info_once() -> None:
    from migratowl.logging_setup import configure_logging

    logger = logging.getLogger("migratowl")
    logger.handlers.clear()
    configure_logging("INFO")
    configure_logging("INFO")  # idempotent: no duplicate handler

    assert logger.getEffectiveLevel() == logging.INFO
    assert len(logger.handlers) == 1
    assert logger.propagate is True  # root-level capture (pytest, basicConfig) keeps working


def test_handler_prints_only_when_root_has_no_handlers(capsys: pytest.CaptureFixture[str]) -> None:
    from migratowl.logging_setup import configure_logging

    root = logging.getLogger()
    saved_root = list(root.handlers)
    logger = logging.getLogger("migratowl")
    logger.handlers.clear()
    configure_logging("INFO")
    try:
        root.handlers.clear()  # like uvicorn: nothing on the root logger
        logging.getLogger("migratowl.pipeline").info("visible-line")
        root.addHandler(logging.NullHandler())  # like basicConfig: root prints it instead
        logging.getLogger("migratowl.pipeline").info("not-duplicated")
    finally:
        root.handlers[:] = saved_root
    err = capsys.readouterr().err
    assert "visible-line" in err
    assert "not-duplicated" not in err


def test_configure_logging_honours_level() -> None:
    from migratowl.logging_setup import configure_logging

    configure_logging("warning")
    assert logging.getLogger("migratowl").getEffectiveLevel() == logging.WARNING


def test_create_app_configures_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    import migratowl.api.main as main_mod

    calls: list[str] = []
    monkeypatch.setattr(main_mod, "configure_logging", calls.append)
    main_mod.create_app(settings=Settings(_env_file=None, log_level="DEBUG"), manager=MagicMock())
    assert calls == ["DEBUG"]
