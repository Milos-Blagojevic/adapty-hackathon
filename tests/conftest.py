import pytest
import responses as responses_lib


@pytest.fixture
def http():
    """Every test runs inside a `responses` mock, so an unmocked request fails instead of leaving the machine."""
    with responses_lib.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock
