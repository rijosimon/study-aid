import pytest

import db


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    db.init_db(":memory:")
