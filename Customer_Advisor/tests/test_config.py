import pytest

from src.config import Settings


@pytest.mark.parametrize("app_mode", ["demo", "personal"])
def test_database_name_is_always_cashy_banking(app_mode):
    settings = Settings(
        _env_file=None,
        app_mode=app_mode,
        db_name="another_database",
        db_name_demo="cashy_banking_demo",
        db_name_personal="another_database",
    )

    assert settings.resolved_db_name == "cashy_banking"
    assert settings.database_url.endswith("/cashy_banking")
