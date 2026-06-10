# Integration Tests for GitHub Module

This directory contains **integration tests** that can optionally access the real GitHub API, as opposed to the unit tests with mocks in `tests/`.

## Why separate integration tests?

- **Unit tests** (`tests/test_*.py`): Fast, use mocks, run in CI/CD by default
- **Integration tests** (`tests/integration/`): Slow, hit real GitHub API, opt-in only

## Setup

### Prerequisites

You need a GitHub personal access token to run integration tests.

**Create a token** at https://github.com/settings/tokens

- Scopes needed: `public_repo` (for public organizations)
- Save the token securely (e.g., in a `.env` file)

### Configure environment

```bash
# Option 1: Set GITHUB_TOKEN environment variable
export GITHUB_TOKEN="ghp_your_token_here"

# Option 2: Create a .env file at the repo root
echo "GITHUB_TOKEN=ghp_your_token_here" > .env
```

## Running integration tests

### Locally (opt-in)

Integration tests are **skipped by default**. To run them:

```bash
# Run all integration tests
pytest tests/integration/ --integration -v

# Run specific integration test file
pytest tests/integration/test_github_fetch_integration.py --integration -v

# Run with explicit token
GITHUB_TOKEN="ghp_..." pytest tests/integration/ --integration -v
```

### In GitHub Actions (CI/CD)

Integration tests are **NOT currently run** in the GitHub Actions CI/CD pipeline. The workflow runs `pytest tests -v` which only executes unit tests with mocks. This is intentional to:

- Avoid rate-limiting issues in CI
- Keep CI fast and reliable
- Prevent unexpected external API calls on every push

## Important notes

- **Locally**: Integration tests are **skipped by default** unless you use the `--integration` flag
- **CI/CD**: Integration tests do **not run** in GitHub Actions
- **Requirements**: Integration tests require a valid GitHub token to run
- **Rate limits**: API calls count against your rate limits (5000 requests/hour for authenticated users)
- **Performance**: Some tests may take longer due to network latency

## Rate limiting

GitHub API has rate limits:

- **Authenticated requests**: 5000/hour per user
- **REST API**: Returns remaining quota in headers
- **GraphQL API**: Returns explicit rate limit query

Integration tests print rate limit info to help you monitor usage:

```
GraphQL rate limit: 4999 points remaining, resets at 2026-06-10T15:23:45Z
```

## Adding more integration tests

1. Create test function with `@pytest.mark.integration` decorator
2. Use the `github_token` fixture from `conftest.py`
3. Make real API calls (the function uses the token automatically)

Example:

```python
@pytest.mark.integration
def test_sync_real_repo(github_token):
    """Test syncing a real repository."""
    repos = get_github_organization_repositories(
        "OpenNeuroDatasets",
        token=github_token,
    )
    assert len(repos) > 0
```

## Continuous Integration

In your CI/CD pipeline (GitHub Actions, etc.):

```yaml
# Skip integration tests in CI by default
- name: Run unit tests
  run: pytest tests/ -v

# Optional: Run integration tests separately with token
- name: Run integration tests (requires secrets)
  if: github.event_name == 'workflow_dispatch'  # Manual trigger only
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  run: pytest tests/integration/ --integration -v
```

## Fixture: `github_token`

The `conftest.py` provides a `github_token` fixture that:

1. Checks for `$GITHUB_TOKEN` environment variable
2. Skips the test if token is not found
3. Passes the token to your test function

This allows graceful skipping when integration tests are run without proper setup.

## Monitoring API usage

Check your API usage at:

- https://api.github.com/rate_limit (requires auth)
- https://github.com/settings/tokens (shows token usage in profile)

## See Also

- [GitHub REST API docs](https://docs.github.com/en/rest)
- [GitHub GraphQL API docs](https://docs.github.com/en/graphql)
- [GitHub rate limiting guide](https://docs.github.com/en/rest/overview/rate-limits-for-the-rest-api)
