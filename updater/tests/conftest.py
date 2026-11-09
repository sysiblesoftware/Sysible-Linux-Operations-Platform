"""Shared fixtures for the updater tests.

Every one of these tests hands `tmp_path` to jobs.start()/start_action() as the
COMPOSE DIRECTORY. In production that is always a real checkout, and compose
cannot do anything in a directory without a compose file — which is the whole of
the second failure reported here:

    no configuration file provided: not found

jobs.py now checks that precondition up front so the operator gets the path it
looked in rather than compose's pathless message. A bare tmp_path is not a stack
compose could ever have acted on, so the fixtures seed one; without it these
tests would be asserting the hand-off against an input production never supplies,
and the new guard would read as a test failure rather than as the check working.
"""
import pytest

_MINIMAL_COMPOSE = """\
services:
  idp:
    image: busybox
"""


@pytest.fixture(autouse=True)
def _a_compose_file_where_the_tests_point(tmp_path):
    """Make tmp_path look like the checkout it stands in for."""
    f = tmp_path / "docker-compose.yml"
    if not f.exists():
        f.write_text(_MINIMAL_COMPOSE)
    return f
