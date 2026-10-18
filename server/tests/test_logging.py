import logging
from collections.abc import Iterator

import pytest

from main import configure_logging


@pytest.fixture
def clean_logger() -> Iterator[logging.Logger]:
    logger = logging.getLogger("iris")
    before = list(logger.handlers)
    logger.handlers[:] = []  # importing main already configured it, on the earlier stderr
    yield logger
    logger.handlers[:] = before


def test_info_lines_reach_stderr_once_even_if_configured_twice(
    clean_logger: logging.Logger, capfd: pytest.CaptureFixture[str]
) -> None:
    configure_logging()
    configure_logging()
    logging.getLogger("iris").info("descriptions on: a test line")
    err = capfd.readouterr().err
    assert err.count("INFO iris: descriptions on: a test line") == 1
    assert clean_logger.propagate  # caplog and the root logger still see it
