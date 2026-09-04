from openparkcad.models import SiteSpec
from openparkcad.rule_profiles import DEFAULT_PROFILE_ID, load_profile, parse_rule_profile, profile_from_site


def test_profile_only_claims_executed_checks() -> None:
    profile = load_profile(DEFAULT_PROFILE_ID)
    assert profile.claims_check("road_traversal_if_requested")
    assert profile.claims_check("traffic_graph")
    assert not profile.claims_check("slope_or_elevation")
    assert "permit_or_code_certification" in profile.unsupported
    assert "jurisdiction_compliance" in profile.human_review_required
    assert profile.source == "software-executed-checks-only"


def test_unknown_profile_is_rejected() -> None:
    try:
        load_profile("imaginary-jurisdiction")
        raise AssertionError("expected unknown profile error")
    except ValueError as exc:
        assert "unknown rule profile" in str(exc)


def test_site_custom_profile_does_not_invent_jurisdiction() -> None:
    site = SiteSpec(name="x", boundary=[(0, 0), (1, 0), (1, 1), (0, 1)], standards={"standard_profile": "custom"})
    profile = profile_from_site(site)
    assert profile.profile_id == DEFAULT_PROFILE_ID
    assert parse_rule_profile({}).profile_id == DEFAULT_PROFILE_ID
