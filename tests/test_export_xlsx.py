from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.database import Base
from app.auth import User
from app.database import get_session


def _make_db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def test_export_xlsx_requires_admin(monkeypatch):
    session = _make_db()

    # create a non-admin user and a fake dependency
    user = User.create_user("normal", "password", role="user")
    session.add(user)
    session.commit()

    def _get_session_override():
        return session

    def _get_user_override():
        return user

    monkeypatch.setattr("app.routers.dashboard.get_session", lambda: _get_session_override())
    monkeypatch.setattr("app.routers.dashboard.get_current_user", lambda: _get_user_override())

    client = TestClient(app)
    resp = client.get("/dashboard/export-xlsx")
    assert resp.status_code == 403


def test_export_xlsx_admin(monkeypatch):
    session = _make_db()

    user = User.create_user("admin", "password", role="admin")
    session.add(user)
    session.commit()

    def _get_session_override():
        return session

    def _get_user_override():
        return user

    monkeypatch.setattr("app.routers.dashboard.get_session", lambda: _get_session_override())
    monkeypatch.setattr("app.routers.dashboard.get_current_user", lambda: _get_user_override())

    client = TestClient(app)
    resp = client.get("/dashboard/export-xlsx")
    assert resp.status_code == 200
    assert resp.headers.get("content-type") == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
