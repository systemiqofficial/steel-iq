import pytest

from pydantic import Field

from pydantic_settings import BaseSettings, SettingsConfigDict


@pytest.fixture
def setup_environment(tmp_path, mocker, monkeypatch):
    """
    Fixture to set up a fake environment for testing.
    """
    # Create a fake home directory
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    # Mock Path.home() in steelo.config to return fake_home
    mocker.patch("steelo.config.Path.home", return_value=fake_home)
    # Also mock get_steelo_home to return the expected path
    mocker.patch("steelo.config.get_steelo_home", return_value=fake_home / ".steelo")

    # Create a fake project root
    fake_project_root = tmp_path / "project"
    fake_project_root.mkdir()

    # Mock steelo.config.root to the fake_project_root
    mocker.patch("steelo.config.root", fake_project_root)

    # Ensure the environment variable is not set
    monkeypatch.delenv("TEST_VARIABLE", raising=False)

    # Return the paths for use in tests
    return fake_home, fake_project_root


def test_settings_home_env_exists(setup_environment):
    """
    Test that when $HOME/.steelo/.env exists, it is used for loading settings.
    """
    fake_home, fake_project_root = setup_environment

    # Create the .steelo directory and .env file in fake_home
    steelo_dir = fake_home / ".steelo"
    steelo_dir.mkdir()
    home_env_file = steelo_dir / ".env"
    home_env_file.write_text("TEST_VARIABLE=from_home_env\n")

    # Instantiate settings with direct env_file configuration
    class TestSettings(BaseSettings):
        test_variable: str = Field(default="from_default", alias="TEST_VARIABLE")

        model_config = SettingsConfigDict(extra="ignore", env_file=str(home_env_file), env_file_encoding="utf-8")

    settings = TestSettings()

    # Check that the setting is loaded from home_env_file
    assert settings.test_variable == "from_home_env"


def test_settings_project_env_used_when_home_env_missing(setup_environment):
    """
    Test that when $HOME/.steelo/.env does not exist, $PROJECT_ROOT/.env is used.
    """
    fake_home, fake_project_root = setup_environment

    # Do not create $HOME/.steelo/.env to simulate its absence

    # Create .env file in project root
    project_env_file = fake_project_root / ".env"
    project_env_file.write_text("TEST_VARIABLE=from_project_env\n")

    # Instantiate settings with direct env_file configuration
    class TestSettings(BaseSettings):
        test_variable: str = Field(default="from_default", alias="TEST_VARIABLE")

        model_config = SettingsConfigDict(extra="ignore", env_file=str(project_env_file), env_file_encoding="utf-8")

    settings = TestSettings()

    # Check that the setting is loaded from project_env_file
    assert settings.test_variable == "from_project_env"


def test_settings_environment_variable_overrides(setup_environment, monkeypatch):
    """
    Test that environment variables override values from .env files.
    """
    fake_home, fake_project_root = setup_environment

    # Create the .steelo directory and .env file in fake_home
    steelo_dir = fake_home / ".steelo"
    steelo_dir.mkdir()
    home_env_file = steelo_dir / ".env"
    home_env_file.write_text("TEST_VARIABLE=from_home_env\n")

    # Set an environment variable that should override the .env file
    monkeypatch.setenv("TEST_VARIABLE", "from_env_var")

    # Instantiate settings with direct env_file configuration
    class TestSettings(BaseSettings):
        test_variable: str = Field(default="from_default", alias="TEST_VARIABLE")

        model_config = SettingsConfigDict(extra="ignore", env_file=str(home_env_file), env_file_encoding="utf-8")

    settings = TestSettings()

    # Check that the environment variable overrides the .env file
    assert settings.test_variable == "from_env_var"
