Here's my plan for this one, following the repro report I posted above.

The short version of the repro: on `2f4e82f`, a profile with no GitHub username, portfolio URL, or resume doesn't crash and doesn't error. `POST /reviews` returned `HTTP 200, "status":"pending"`, and two seconds later `GET /reviews/$RID` returned `"status":"complete","overall_score":0.81`. The server log had `ingestion_pipeline_completed … sources_count=0` followed by `review_processing_completed overall_score=0.81`. `tests/unit/test_review_routes.py` doesn't exist, and grepping `tests/` for `"/reviews` or `process_review` returns nothing.

I read the path the request takes to see why. `create_review_endpoint` in `api/routes/reviews.py` never loads the profile, so it has no way to reject anything. `process_review` logs `sources_count` and continues whatever the number is. The agent and RAG steps are placeholders that return fixed sections and a fixed `0.81` without reading the ingestion results, which matches the exact score I got back. So the test the issue describes ("returns an appropriate error") can't pass on the current code. The endpoint needs a small guard first.

@Aburke225, in my repro comment I asked whether the error should come from `POST /reviews` (option 1) or from `process_review` marking the review `failed` (option 2). I haven't seen a reply, so I'm planning on option 1. The issue says "the endpoint returns an appropriate error", and the POST response is the only place the endpoint itself can do that. If you'd prefer option 2, tell me and I'll change the plan before building.

What I'll change:

- `api/routes/reviews.py`, in `create_review_endpoint`: load the profile with the existing `get_profile`, and if it has no `github_username`, `portfolio_url`, or `resume_text`, return `422` with `"Profile has no content to review. Add a GitHub username, portfolio URL, or resume first."` No review is created and no background task is queued. I picked 422 because `api/routes/profiles.py` already uses it for requests the server can't use.
- `tests/unit/test_review_routes.py` (new): one test that an empty profile gets the 422 and `create_review` is never called, and a control test that a profile with a `github_username` still gets `200` / `"pending"`. Mocked DB, marked `unit`.
- `docs/API.md`: one line under `POST /reviews` for the 422.

I'm leaving alone `process_review` and the placeholder pipeline, the frontend, what happens for a missing profile or someone else's, and the xfail tests from #65.

How I'll check it:

- `.venv/bin/pytest tests/unit/test_review_routes.py -v -m unit`. The empty-profile test fails with `200 != 422` before the guard, and both tests pass after.
- My repro steps again on the branch. Step 3 should return `HTTP 422` with the detail above, where it returned `HTTP 200, "status":"pending"` before. `select count(*) from reviews where profile_id='$PID';` should be `0`, and the log should have no `review_processing_started` for that profile.
- A control my repro didn't have: the same steps with `-F github_username=octocat` should still go `pending` then `complete`.

A few things I'm not sure about:

- Whether a behavior change is welcome on an issue labeled `tests`. It's about five lines, but it is more than a test.
- The frontend. Reading `NewProfilePage.tsx`, an error from `createReview` logs to the console and sends the user to the dashboard with no message. I've only read that code, not run it.
- While reading `_run_ingestion_pipeline` I noticed it calls `IngestedSource(..., raw_data=...)`, and calling that constructor by itself raises `TypeError: 'raw_data' is an invalid keyword argument for IngestedSource`. The pipeline catches and logs it, so I think `ingested_sources` may stay empty even for profiles that have content. I haven't run the full pipeline to confirm. That's why my guard checks the profile fields and not the table. I'm not touching it here, but I can open a separate issue once the control run confirms it.

The branch will be `fix/36-review-no-ingested-content` on my fork.
