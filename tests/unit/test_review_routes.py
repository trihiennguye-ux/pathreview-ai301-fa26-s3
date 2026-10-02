"""Tests for api/routes/reviews.py"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.middleware.auth import get_current_user
from api.routes.reviews import router
from core.database import get_db

NO_CONTENT_DETAIL = (
    "Profile has no content to review. Add a GitHub username, portfolio URL, or resume first."
)


@pytest.mark.unit
class TestCreateReviewEndpoint:
    """Test suite for POST /reviews."""

    @pytest.fixture
    def user(self):
        """Create a fake authenticated user."""
        return SimpleNamespace(id=uuid4())

    @pytest.fixture
    def client(self, user):
        """Build an app around the reviews router with auth and the database faked."""
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = lambda: AsyncMock()
        return TestClient(app)

    @staticmethod
    def _profile(**fields):
        """Create a fake Profile with no sources unless given."""
        defaults = {"github_username": None, "portfolio_url": None, "resume_text": None}
        return SimpleNamespace(id=uuid4(), **{**defaults, **fields})

    @staticmethod
    def _pending_review(profile_id):
        """Create a fake Review as create_review returns it."""
        now = datetime.now(UTC)
        return SimpleNamespace(
            id=uuid4(),
            profile_id=profile_id,
            status="pending",
            sections=None,
            overall_score=None,
            error_message=None,
            created_at=now,
            updated_at=now,
        )

    def test_create_review_rejects_profile_with_no_content(self, client):
        """A profile with no GitHub username, portfolio URL, or resume gets a 422."""
        profile = self._profile()

        with (
            patch("api.routes.reviews.get_profile", AsyncMock(return_value=profile)),
            patch(
                "api.routes.reviews.create_review",
                AsyncMock(return_value=self._pending_review(profile.id)),
            ) as create_review,
            patch("api.routes.reviews.process_review", AsyncMock()),
        ):
            response = client.post("/reviews", json={"profile_id": str(profile.id)})

        assert response.status_code == 422
        assert response.json() == {"detail": NO_CONTENT_DETAIL}
        create_review.assert_not_awaited()

    def test_create_review_accepts_profile_with_content(self, client):
        """A profile with at least one source is still accepted as pending."""
        profile = self._profile(github_username="octocat")

        with (
            patch("api.routes.reviews.get_profile", AsyncMock(return_value=profile)),
            patch(
                "api.routes.reviews.create_review",
                AsyncMock(return_value=self._pending_review(profile.id)),
            ),
            patch("api.routes.reviews.process_review", AsyncMock()),
        ):
            response = client.post("/reviews", json={"profile_id": str(profile.id)})

        assert response.status_code == 200
        assert response.json()["status"] == "pending"
