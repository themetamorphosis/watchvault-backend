import os
import uuid
import asyncio
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from sqlalchemy.pool import NullPool

# Override settings before importing the app
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only-32chars!!"
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://watchvault:watchvault@localhost:5432/watchvault_db_test",
)
os.environ["TMDB_API_KEY"] = "test_tmdb_key"
os.environ["FRONTEND_URL"] = "http://localhost:3000"

from app.db.database import Base  # noqa: E402
from app.core import security  # noqa: E402
from app.db import models  # noqa: E402
from app.api import dependencies  # noqa: E402


TEST_ENGINE = create_async_engine(os.environ["DATABASE_URL"], echo=False, poolclass=NullPool)
TestSessionLocal = async_sessionmaker(
    bind=TEST_ENGINE,
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db():
        yield db

    from app.main import app
    app.dependency_overrides[dependencies.get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(db: AsyncSession) -> models.User:
    user_id = str(uuid.uuid4())
    user = models.User(
        id=user_id,
        name="Test User",
        email=f"test-{user_id[:8]}@example.com",
        password=security.get_password_hash("TestPass123!"),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def auth_headers(test_user: models.User) -> dict:
    from datetime import timedelta
    token = security.create_access_token(
        subject=test_user.email,
        expires_delta=timedelta(minutes=30),
    )
    return {"Authorization": f"Bearer {token}"}
