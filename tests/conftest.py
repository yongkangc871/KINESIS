import sys
from pathlib import Path

import pytest

HOST = Path(__file__).resolve().parent.parent / "host"
sys.path.insert(0, str(HOST))

from wristmon.config import load_config  # noqa: E402


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture(scope="session")
def long_set_cfg():
    """One long set so every scripted rep lands in the same set."""
    return load_config(overrides={"exercise": {"reps_per_set": 1000, "max_attempts_per_set": 1000,
                                               "max_reps_per_session": 1000}})
