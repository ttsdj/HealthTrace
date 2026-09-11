import pytest

from backend.observability.metrics import reset_process_metrics
from backend.security.rate_limit import limiter

# Must satisfy two checks at the same time, so keep the `local_` prefix:
#   - backend.infra.auth.is_placeholder_secret rejects anything under 32
#     characters or containing a placeholder marker;
#   - scripts/check_repo_safety.py only exempts literal values that start with
#     replace/your_/local_/example/postgres.
# Dropping the `local_` prefix makes this read like a real key, which is what
# the safety scan rejects, so the prefix is load-bearing.
TEST_JWT_SECRET = "local_healthtrace_test_jwt_secret_0123456789"


@pytest.fixture(autouse=True)
def _default_jwt_secret(monkeypatch):
    """Give every test a usable signing key.

    backend.infra.auth.jwt_secret_key() no longer falls back to a guessable
    default, so any test that authenticates has to provide a key. Real
    deployments set JWT_SECRET_KEY in the environment instead.
    """
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_JWT_SECRET)


@pytest.fixture(autouse=True)
def _reset_process_globals():
    """Keep per-process abuse counters and metric series out of test outcomes.

    Both live in module globals.  Without this the registration rate limit would
    trip partway through the suite, so whether a test passed would depend on how
    many other tests happened to run first.
    """
    reset_process_metrics()
    limiter.reset()
    yield
    reset_process_metrics()
    limiter.reset()
