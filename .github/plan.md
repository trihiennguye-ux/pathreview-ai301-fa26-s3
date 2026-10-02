# Plan for issue #36: no test for `POST /reviews` on a profile with no ingested documents

Issue: https://github.com/codepath/pathreview-ai301-fa26-s3/issues/36
Branch: `fix/36-review-no-ingested-content` on my fork, based on `main` at `2f4e82f` (the same commit I ran my repro on).

## Problem

The issue asks for a test showing that the review endpoint "returns an appropriate error rather than crashing" when a profile exists but has no ingested content. It names `tests/unit/test_review_routes.py`.

That file doesn't exist, and nothing in `tests/` touches `POST /reviews` or `process_review`. So the test is missing, as the issue says.

The harder part is that the test can't pass as described on the current code. In my repro (posted on the issue 2026-09-26) I created a profile with no GitHub username, portfolio URL, or resume, and asked for a review:

- `POST /reviews` returned `HTTP 200, "status":"pending"`.
- `select count(*) from ingested_sources where profile_id='$PID';` returned `0`.
- Two seconds later `GET /reviews/$RID` returned `"status":"complete","overall_score":0.81` with three feedback sections.
- The server log went `review_processing_started`, `ingestion_pipeline_completed sources_count=0`, `agent_orchestration_completed`, `review_processing_completed overall_score=0.81`.

So the endpoint neither errors nor crashes. It hands back a finished review for a profile with nothing in it.

## Cause

I read the request path on `2f4e82f` to see why.

`create_review_endpoint` in `api/routes/reviews.py` never loads the profile. It calls `create_review` and queues `process_review`, so there is nothing that could reject the request. That explains the 200.

`process_review` in `core/services/review_service.py` logs `sources_count` and keeps going whatever the number is. That explains `sources_count=0` being followed by `review_processing_completed`.

`_run_agent_orchestration` and `_run_rag_retrieval_generation` are placeholders. They return fixed sections and a fixed score of `0.81` and don't read the ingestion results. That explains why I got exactly `0.81` and the canned feedback text.

Put together: an empty profile ends up "complete" because nothing between the route and the placeholder generator checks whether there is any content.

My repro didn't include a control (a profile that does have content). I'm adding one below.

## What I'll change

I'm going with one approach: reject the request at the endpoint. The issue says "the endpoint returns an appropriate error", and the `POST /reviews` response is the only place the endpoint itself can return one, because processing happens later in a background task.

1. `api/routes/reviews.py`, in `create_review_endpoint`, at the top of the `try` before `create_review` is called. Import `get_profile` from `core.services.profile_service` and add:

   ```python
   profile = await get_profile(db=db, profile_id=data.profile_id, user_id=current_user.id)
   if profile and not (profile.github_username or profile.portfolio_url or profile.resume_text):
       log.warning("review_rejected_no_content", profile_id=str(data.profile_id))
       raise HTTPException(
           status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
           detail="Profile has no content to review. Add a GitHub username, portfolio URL, or resume first.",
       )
   ```

   No review is created and no background task is queued. The endpoint already has `except HTTPException: raise`, so the 422 gets through without touching the error handling.

   I'm treating "no ingested content" as those three profile fields being empty. They are the same three things `_run_ingestion_pipeline` branches on, so the guard fires in exactly the cases where the log would say `sources_count=0`. I picked 422 because `api/routes/profiles.py` already uses it for requests that are well formed but that the server can't use (wrong resume file type, unparseable PDF).

2. `tests/unit/test_review_routes.py`, new, with two tests marked `@pytest.mark.unit`. The file builds a small `FastAPI()` app around `api.routes.reviews.router`, overrides `get_current_user` and `get_db` through `app.dependency_overrides`, and patches `get_profile`, `create_review`, and `process_review` in `api.routes.reviews`, so it needs no database.

   - `test_create_review_rejects_profile_with_no_content`: profile with all three fields `None`. Expects status `422`, the detail string above, and `create_review` never awaited.
   - `test_create_review_accepts_profile_with_content`: profile with `github_username="octocat"`. Expects `200` and `"status": "pending"`.

3. `docs/API.md`: one line under `POST /reviews` mentioning the 422.

I'll write the first test before the guard and watch it fail, then add the guard.

## What I'm leaving alone

- `process_review` and the placeholder pipeline steps. I'm not adding a `failed` / `error_message` path there. That was option 2 in my repro comment.
- A profile that doesn't exist or belongs to someone else. The guard only fires when the profile is found for the current user. Anything else behaves as it does today.
- The `IngestedSource(..., raw_data=...)` calls in `_run_ingestion_pipeline`. I think they are broken (see below), but that is a different bug.
- The frontend.
- The xfail tests in `tests/unit/test_review_service.py` (#65), `tests/conftest.py`, CI, and the chromadb/NumPy startup failure from my repro environment.

## How I'll know it works

Unit tests:

```
.venv/bin/pytest tests/unit/test_review_routes.py -v -m unit
```

Before the guard, `test_create_review_rejects_profile_with_no_content` fails on `assert 200 == 422`. After the guard, `2 passed`. Then `make test-unit` to check the passed/xfailed counts elsewhere haven't moved.

Then my repro steps again on the branch, same setup (`docker compose up -d`, `make setup`, backend on port 8000, logged in as `user1@example.com`):

- Step 2, `POST /profiles` with no fields: same as before, a profile with the three fields null.
- Step 3, `POST /reviews` with that profile id, with `-w '%{http_code}'` added to the curl: `HTTP 422` and body `{"detail":"Profile has no content to review. Add a GitHub username, portfolio URL, or resume first."}`. On `main` this was `HTTP 200, "status":"pending"`.
- Step 4 no longer applies because step 3 returns no review id. In its place, `select count(*) from reviews where profile_id='$PID';` should return `0`.
- Server log: one `review_rejected_no_content` line, and no `review_processing_started` for that profile.

Control, which my original repro didn't have: the same steps with `-F github_username=octocat` on step 2. Step 3 should still return `HTTP 200, "status":"pending"` and step 4 `"status":"complete"`, the same as `main` today.

## Things I'm not sure about

The maintainer hasn't answered. In my repro comment I asked @Aburke225 whether the error should come from `POST /reviews` (option 1) or from `process_review` marking the review `failed` (option 2). No reply as of 2026-10-02. I'm going with option 1 because it matches the wording of the issue. If they want option 2, the guard moves into `process_review`, the test changes, and I'll write that up under Deviations.

This is labeled as a test task and I'm changing endpoint behavior. I don't see a way around it, since the repro shows 200 and "complete", but it is about five lines more than a test and a maintainer might not want that.

The frontend will handle the 422 badly. `frontend/src/pages/NewProfilePage.tsx` calls `createReview` right after creating a profile, and on any error it does `console.error` and `navigate('/dashboard')`. So someone who submits an empty profile form would land on the dashboard with no message. I only read that code. I haven't run the frontend.

`ingested_sources` may be empty for every profile, not only empty ones. `_run_ingestion_pipeline` calls `IngestedSource(..., raw_data=...)` and the model has no `raw_data` column. I ran that constructor by itself in the project venv and got `TypeError: 'raw_data' is an invalid keyword argument for IngestedSource`. The pipeline catches the exception and logs it. I haven't run the whole pipeline on a profile with content, so I can't say for sure the table stays empty. This is why the guard looks at the profile fields and not the table, and it means the `count = 0` in my repro might not tell empty profiles apart from full ones. The control run will show which, and I'll note what I find.

A `github_username` of `" "` would get past the guard. I haven't checked whether `POST /profiles` can store that.

I haven't confirmed that `api.routes.reviews` imports cleanly in a unit test with no database or `.env` (it imports `core.database`). If it doesn't, I'll record that under Deviations before changing anything.

I'm on Python 3.13.7 and CI may be on 3.11. I don't know of anything version-specific in the test.

## Deviations

Nothing yet. I'll fill this in during the build.
