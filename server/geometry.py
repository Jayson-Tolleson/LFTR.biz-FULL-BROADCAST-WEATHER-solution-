
def validate_feature_path(path):
    if not isinstance(path, list) or len(path) < 3:
        return None, "empty_path"
    out = []
    for p in path:
        lat = float(p.get("lat"))
        lng = float(p.get("lng"))
        alt = max(0.0, min(18000.0, float(p.get("alt", 0))))
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return None, "invalid_path"
        out.append({"lat": lat, "lng": lng, "alt": alt})
    return out, None
