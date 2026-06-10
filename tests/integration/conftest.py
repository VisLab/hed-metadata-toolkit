"""
Integration tests for GitHub module (optional).

These tests can hit the real GitHub API when run with --integration flag.
They require a GitHub token (set GITHUB_TOKEN environment variable).

Usage:
    pytest tests/integration/ --integration -v
    GITHUB_TOKEN=<token> pytest tests/integration/ --integration -v

To run only integration tests for fetch_repo_list:
    pytest tests/integration/test_github_fetch_integration.py --integration -v

To skip integration tests (default):
    pytest tests/  -v
"""

import os

import pytest
from dotenv import load_dotenv

# Load .env file so GITHUB_TOKEN is available
load_dotenv()


def pytest_addoption(parser):
    """Add --integration command line option."""
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="run integration tests (require real GitHub API calls and GITHUB_TOKEN)",
    )


def pytest_configure(config):
    """Register the --integration marker."""
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests (require real GitHub API calls)",
    )


@pytest.fixture(scope="session")
def github_token(request):
    """
    Get GitHub token from environment.

    Raises pytest skip if --integration is not used or token is not available.
    """
    if not request.config.getoption("--integration"):
        pytest.skip("need --integration option to run")

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        pytest.skip("GITHUB_TOKEN not set; skipping integration tests")
    return token


def pytest_runtest_setup(item):
    """Skip integration tests unless --integration flag is used."""
    if "integration" in item.keywords:
        if not item.config.getoption("--integration", default=False):
            pytest.skip("need --integration option to run")
