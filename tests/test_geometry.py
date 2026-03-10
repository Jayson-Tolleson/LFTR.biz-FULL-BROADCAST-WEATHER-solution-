from server.geometry import validate_feature_path


def test_geometry_validation():
    good, reason = validate_feature_path([{"lat": 1, "lng": 1, "alt": 99999}, {"lat": 2, "lng": 2, "alt": 50}, {"lat": 3, "lng": 3, "alt": 60}])
    assert reason is None
    assert good[0]["alt"] == 18000.0
