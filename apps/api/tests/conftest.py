"""Repository-wide test configuration — loaded before any test module.

Sets `DATABASE_URL` from `DATABASE_TEST_URL` BEFORE any `app.*` import so the
lazily-created engine always targets the throwaway test database
(`postgres-test`, :5433), regardless of which test directory is collected
first. Tests must never touch the dev database.
"""

import os

_DEFAULT_TEST_URL = (
    "postgresql+asyncpg://workpulse:workpulse@localhost:5433/workpulse_test"
)
TEST_DATABASE_URL = os.environ.get("DATABASE_TEST_URL", _DEFAULT_TEST_URL)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
