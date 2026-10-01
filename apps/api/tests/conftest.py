"""Repository-wide test configuration — loaded before any test module.

Sets `DATABASE_URL` from `DATABASE_TEST_URL` and `REDIS_URL` from
`REDIS_TEST_URL` BEFORE any `app.*` import so the lazily-created clients
always target the throwaway services (`postgres-test`, :5433; `redis-test`,
:6380), regardless of which test directory is collected first. Tests must
never touch the dev database or the dev Redis.
"""

import os

_DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://workpulse:workpulse@localhost:5433/workpulse_test"
)
TEST_DATABASE_URL = os.environ.get("DATABASE_TEST_URL", _DEFAULT_TEST_DATABASE_URL)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

_DEFAULT_TEST_REDIS_URL = "redis://localhost:6380/0"
TEST_REDIS_URL = os.environ.get("REDIS_TEST_URL", _DEFAULT_TEST_REDIS_URL)
os.environ["REDIS_URL"] = TEST_REDIS_URL
