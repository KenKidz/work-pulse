"""Strict boot configuration validation (deployment T2)."""

import pytest

from app.shared.boot import (
    REQUIRED_BOOT_KEYS,
    STRICT_CONFIG_FLAG,
    validate_boot_configuration,
)


def _complete_env() -> dict[str, str]:
    env = {key: f"value-for-{key}" for key in REQUIRED_BOOT_KEYS}
    env[STRICT_CONFIG_FLAG] = "1"
    return env


def test_flag_unset_is_a_no_op() -> None:
    validate_boot_configuration({})


def test_flag_disabled_is_a_no_op() -> None:
    env = {STRICT_CONFIG_FLAG: "0"}

    validate_boot_configuration(env)


def test_all_required_keys_present_is_a_no_op() -> None:
    validate_boot_configuration(_complete_env())


def test_missing_key_exits_one_naming_only_the_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    env = _complete_env()
    del env["DATABASE_URL"]

    with pytest.raises(SystemExit) as excinfo:
        validate_boot_configuration(env)

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == "missing configuration: DATABASE_URL"
    assert captured.out == ""
    # Never a value, never a traceback.
    assert "value-for-" not in captured.err


def test_blank_key_counts_as_missing(capsys: pytest.CaptureFixture[str]) -> None:
    env = _complete_env()
    env["STORAGE_SECRET_KEY"] = "   "

    with pytest.raises(SystemExit) as excinfo:
        validate_boot_configuration(env)

    assert excinfo.value.code == 1
    assert (
        capsys.readouterr().err.strip() == "missing configuration: STORAGE_SECRET_KEY"
    )


def test_first_missing_key_wins_in_documented_order(
    capsys: pytest.CaptureFixture[str],
) -> None:
    env = _complete_env()
    del env["REDIS_URL"]
    del env["CLICKHOUSE_URL"]

    with pytest.raises(SystemExit):
        validate_boot_configuration(env)

    assert capsys.readouterr().err.strip() == "missing configuration: REDIS_URL"
