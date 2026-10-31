import pytest

from note import fakerun


@pytest.fixture
def fake_run(tmp_path):
    return fakerun.make(tmp_path / "run-fake")
