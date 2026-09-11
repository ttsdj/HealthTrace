"""Regression tests for the abuse-resistance fixes.

Each test failed before its fix:

* an unmatched path used to become a metric label verbatim, so one series per
  probed URL accumulated in process memory forever;
* ``/auth/login`` and ``/auth/register`` had no rate limit at all;
* an unauthenticated request to an unmatched path under an audited prefix wrote
  an audit row, so random URLs could fill the audit table.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import create_app
from backend.db.models import AuditEvent
from backend.infra.auth import get_db
from backend.infra.database import Base
from backend.observability import metrics
from backend.security import rate_limit


def _app_client(tmp_path, name="abuse-hardening.db"):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), Session


def _route_labels(rendered: str) -> set[str]:
    labels = set()
    for line in rendered.splitlines():
        if not line.startswith("healthtrace_http_requests_total{"):
            continue
        for part in line.split("{", 1)[1].split("}", 1)[0].split(","):
            key, _, value = part.partition("=")
            if key == "route":
                labels.add(value)
    return labels


# --------------------------------------------------------------------------
# Metric labels must come from a bounded set
# --------------------------------------------------------------------------


def test_recorded_routes_are_capped_and_overflow_is_folded(monkeypatch):
    monkeypatch.setattr(metrics, "_MAX_ROUTE_SERIES", 3)

    for index in range(50):
        metrics.record_http_request(
            method="GET",
            route=f"/scanned-{index}",
            status_code=404,
            duration_seconds=0.01,
        )

    labels = _route_labels(metrics.render_process_metrics())
    # Three distinct templates plus the overflow bucket -- not fifty series.
    assert labels == {'"/scanned-0"', '"/scanned-1"', '"/scanned-2"', '"/other"'}


def test_non_path_route_values_collapse_to_one_label():
    for value in ("", "not-a-path", "   ", "http://evil.example/"):
        metrics.record_http_request(
            method="GET", route=value, status_code=400, duration_seconds=0.01
        )

    assert _route_labels(metrics.render_process_metrics()) == {
        f'"{metrics.UNMATCHED_ROUTE}"'
    }


def test_unmatched_requests_share_one_route_label_end_to_end(tmp_path):
    """Walking random URLs must not create a series per URL."""
    client, _ = _app_client(tmp_path)

    for index in range(5):
        assert client.get(f"/no-such-route-{index}").status_code == 404

    labels = _route_labels(metrics.render_process_metrics())
    assert labels == {f'"{metrics.UNMATCHED_ROUTE}"'}


# --------------------------------------------------------------------------
# Credential endpoints must be rate limited
# --------------------------------------------------------------------------


def test_login_is_rate_limited_per_client(tmp_path, monkeypatch):
    monkeypatch.setattr(rate_limit, "_LOGIN_LIMIT", 2)
    monkeypatch.setattr(rate_limit, "_LOGIN_WINDOW_SECONDS", 3600)
    client, _ = _app_client(tmp_path)

    # `example-` keeps scripts/check_repo_safety.py from reading this as a secret.
    bad = {"username": "nobody", "password": "example-wrong-password"}
    assert client.post("/auth/login", json=bad).status_code == 401
    assert client.post("/auth/login", json=bad).status_code == 401

    blocked = client.post("/auth/login", json=bad)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1


def test_registration_is_rate_limited_per_client(tmp_path, monkeypatch):
    """Registration provisions four rows, so it is the cheaper request to abuse."""
    monkeypatch.setattr(rate_limit, "_REGISTER_LIMIT", 1)
    monkeypatch.setattr(rate_limit, "_REGISTER_WINDOW_SECONDS", 3600)
    client, Session = _app_client(tmp_path)

    first = client.post(
        "/auth/register",
        json={"username": "abuse-one", "password": "example-test-password"},
    )
    assert first.status_code == 200

    blocked = client.post(
        "/auth/register",
        json={"username": "abuse-two", "password": "example-test-password"},
    )
    assert blocked.status_code == 429
    with Session() as db:
        assert db.query(AuditEvent).count() > 0


def test_limiter_table_is_bounded_and_fails_closed():
    limiter = rate_limit.FixedWindowLimiter(max_keys=2)
    kwargs = {"bucket": "login", "limit": 10, "window_seconds": 3600}

    assert limiter.check(key="a", **kwargs).allowed
    assert limiter.check(key="b", **kwargs).allowed
    # A third client while the table is full is refused rather than growing the
    # table without limit.
    assert not limiter.check(key="c", **kwargs).allowed

    limiter.reset()
    assert limiter.check(key="c", **kwargs).allowed


def test_expired_windows_are_evicted_so_the_table_recovers():
    limiter = rate_limit.FixedWindowLimiter(max_keys=2)
    kwargs = {"bucket": "login", "limit": 10, "window_seconds": 60}

    assert limiter.check(key="a", now=0.0, **kwargs).allowed
    assert limiter.check(key="b", now=0.0, **kwargs).allowed
    # A later window makes the first two entries stale, so the table drains
    # instead of staying permanently full after one burst.
    assert limiter.check(key="c", now=120.0, **kwargs).allowed


def test_proxy_headers_are_only_trusted_when_configured(monkeypatch):
    from starlette.requests import Request

    def build(xff: str) -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/auth/login",
                "headers": [(b"x-forwarded-for", xff.encode())],
                "client": ("10.0.0.9", 1234),
                "app": None,
            }
        )

    monkeypatch.setattr(rate_limit, "_TRUST_PROXY_HEADERS", False)
    # Spoofing the header must not buy a fresh bucket by default.
    assert rate_limit.client_key(build("1.2.3.4")) == "10.0.0.9"

    monkeypatch.setattr(rate_limit, "_TRUST_PROXY_HEADERS", True)
    assert rate_limit.client_key(build("1.2.3.4, 10.0.0.7")) == "10.0.0.7"


# --------------------------------------------------------------------------
# Unmatched paths must not write audit rows
# --------------------------------------------------------------------------


def test_unmatched_audited_paths_do_not_write_audit_rows(tmp_path):
    """These requests reached no endpoint, so there is no access to account for."""
    client, Session = _app_client(tmp_path)

    for index in range(5):
        assert client.get(f"/patient/no-such-record-{index}").status_code == 404
        assert client.get(f"/auth/no-such-endpoint-{index}").status_code == 404

    with Session() as db:
        assert db.query(AuditEvent).count() == 0


def test_matched_denials_are_still_audited(tmp_path):
    """The fix must not silence the records an audit log exists to keep."""
    client, Session = _app_client(tmp_path)

    denied = client.get("/patient/facts")
    assert denied.status_code == 401

    with Session() as db:
        events = db.query(AuditEvent).all()
        assert len(events) == 1
        assert events[0].outcome == "denied"
        assert events[0].actor_user_id is None
