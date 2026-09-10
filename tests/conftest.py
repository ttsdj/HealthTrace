import pytest

# At least 32 characters and free of the placeholder markers rejected by
# backend.infra.auth.is_placeholder_secret.
TEST_JWT_SECRET = "healthtrace-test-jwt-secret-key-0123456789"


@pytest.fixture(autouse=True)
def _default_jwt_secret(monkeypatch):
    """Give every test a usable signing key.

    backend.infra.auth.jwt_secret_key() no longer falls back to a guessable
    default, so any test that authenticates has to provide a key. Real
    deployments set JWT_SECRET_KEY in the environment instead.
    """
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_JWT_SECRET)
