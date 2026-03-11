from __future__ import annotations

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from server.gfs_service import GFSService
from server import routes as routes_module
from server import app_factory as app_factory_module


def test_derive_real_source_fields_cloud_layers_match_precip(monkeypatch, tmp_path):
    svc = GFSService(str(tmp_path))

    precip = np.ones((721, 1440), dtype=float)
    low = np.full((721, 1440), 0.2, dtype=float)
    mid = np.full((721, 1440), 0.4, dtype=float)
    high = np.full((721, 1440), 0.6, dtype=float)
    lat2d = np.zeros((721, 1440), dtype=float)
    lon2d = np.zeros((721, 1440), dtype=float)

    monkeypatch.setattr(svc, "extract_precip_rate_mm_hr", lambda groups: precip)
    monkeypatch.setattr(svc, "derive_cloud_layers", lambda *args, **kwargs: {"low": low, "mid": mid, "high": high})
    monkeypatch.setattr(svc, "derive_balloon_vectors", lambda groups: [])
    monkeypatch.setattr(svc, "ensure_lat_lon_2d", lambda sample: (lat2d, lon2d))
    monkeypatch.setattr(svc, "_extract_scalar_field", lambda groups, candidates: None)

    fields = svc._derive_real_source_fields({"surface": object()})

    precip_shape = fields["precip"].shape
    assert fields["cloud_layers"]["low"].shape == precip_shape
    assert fields["cloud_layers"]["mid"].shape == precip_shape
    assert fields["cloud_layers"]["high"].shape == precip_shape


def test_derive_real_hazard_payloads_shape_consistent(monkeypatch, tmp_path):
    svc = GFSService(str(tmp_path))
    shape = (241, 480)

    precip = np.ones(shape, dtype=float)
    cloud_layers = {
        "low": np.ones(shape, dtype=float) * 0.1,
        "mid": np.ones(shape, dtype=float) * 0.2,
        "high": np.ones(shape, dtype=float) * 0.3,
    }
    lat2d = np.zeros(shape, dtype=float)
    lon2d = np.zeros(shape, dtype=float)

    monkeypatch.setattr(svc, "threshold_to_mask", lambda arr, _: np.asarray(arr) > 0.1)
    monkeypatch.setattr(svc, "derive_hail_mask", lambda groups, p, c, **kwargs: np.asarray(p) > 0.2)
    monkeypatch.setattr(svc, "derive_lightning_mask", lambda groups, p, c, **kwargs: np.asarray(p) > 0.3)
    monkeypatch.setattr(svc, "connected_components_or_simple_cell_polygons", lambda *args, **kwargs: [{"ok": True}])

    out = svc._derive_real_hazard_payloads(
        {},
        {"precip": precip, "cloud_layers": cloud_layers, "lat2d": lat2d, "lon2d": lon2d},
    )

    assert out["rain"]["count"] == 1
    assert out["hail"]["count"] == 1
    assert out["lightning"]["count"] == 1


def test_assert_same_shape_mismatch_raises(tmp_path):
    svc = GFSService(str(tmp_path))
    with pytest.raises(ValueError, match="shape mismatch"):
        svc._assert_same_shape("hazard_fields", np.zeros((10, 10)), np.zeros((5, 5)))


def test_static_file_rejects_static_path_that_is_not_directory(monkeypatch, tmp_path):
    broken_static_path = tmp_path / "static"
    broken_static_path.write_text("not a directory")
    monkeypatch.setattr(routes_module, "STATIC_DIR", broken_static_path)

    with pytest.raises(NotADirectoryError):
        routes_module._static_file("index.html")


def test_indexgfs_loader_and_polygon_api_usage():
    html = Path("static/indexgfs.html").read_text(encoding="utf-8")

    assert "ensureGoogleMapsBootstrap" in html
    assert "loadGoogleMapsLibraries" in html
    assert "importLibrary('maps3d')" in html
    assert "maps/api/js?key=" not in html

    start = html.index("const polygon = new Ctor({")
    end = html.index("});", start)
    polygon_ctor = html[start:end]
    assert "path:" in polygon_ctor
    assert "innerPaths:" in polygon_ctor
    assert "outerCoordinates:" not in polygon_ctor
    assert "innerCoordinates:" not in polygon_ctor


def test_indexgfs_polygon_validation_and_budget_guards_present():
    html = Path("static/indexgfs.html").read_text(encoding="utf-8")

    assert "function sanitizeRing(" in html
    assert "function sanitizeHoles(" in html
    assert "function crossesAntimeridian(" in html
    assert "function validatePolygonSpec(" in html
    assert "function allowPolygonByBudget(" in html
    assert "maxPolygonsPerTileLayer" in html
    assert "maxVerticesPerPolygon" in html
    assert "maxTotalVerticesPerPass" in html


def test_indexgfs_single_altitude_mode_policy_present():
    html = Path("static/indexgfs.html").read_text(encoding="utf-8")
    assert "const map = {" in html
    assert "cloud: AltitudeMode.ABSOLUTE" in html
    assert "rain: AltitudeMode.RELATIVE_TO_GROUND" in html
    assert "surface: AltitudeMode.CLAMP_TO_GROUND" in html
    assert "modeFallback" not in html


def test_coerce_field_to_canonical_grid_resamples_to_target(tmp_path):
    svc = GFSService(str(tmp_path))
    coarse = np.arange(241 * 480, dtype=float).reshape(241, 480)
    out = svc._coerce_field_to_canonical_grid(coarse, (721, 1440), "cape")
    assert out.shape == (721, 1440)
    assert np.isfinite(out).all()


def test_derive_hail_mask_coerces_mixed_grid_inputs(monkeypatch, tmp_path):
    svc = GFSService(str(tmp_path))
    precip = np.full((721, 1440), 6.0, dtype=float)
    cloud_layers = {"high": np.full((241, 480), 0.9, dtype=float)}

    monkeypatch.setattr(svc, "safe_data_var", lambda ds, names: object())

    class _Arr:
        values = np.full((241, 480), 1200.0, dtype=float)

    monkeypatch.setattr(svc, "squeeze_forecast_array", lambda da, preserve_dims=(): _Arr())

    mask = svc.derive_hail_mask({"surface": object()}, precip, cloud_layers, target_shape=(721, 1440))
    assert mask.shape == (721, 1440)
    assert mask.dtype == np.bool_


def test_derive_hail_mask_named_shape_mismatch_when_coercion_bypassed(monkeypatch, tmp_path):
    svc = GFSService(str(tmp_path))
    precip = np.full((721, 1440), 6.0, dtype=float)
    cloud_layers = {"high": np.full((241, 480), 0.9, dtype=float)}

    monkeypatch.setattr(svc, "safe_data_var", lambda ds, names: object())

    class _Arr:
        values = np.full((241, 480), 1200.0, dtype=float)

    monkeypatch.setattr(svc, "squeeze_forecast_array", lambda da, preserve_dims=(): _Arr())
    monkeypatch.setattr(svc, "_coerce_field_to_canonical_grid", lambda field, target_shape, field_name, warned=None: np.asarray(field))

    with pytest.raises(ValueError, match=r"hail_mask_shape_mismatch cape=\(241, 480\) precip=\(721, 1440\) deep=\(241, 480\)"):
        svc.derive_hail_mask({"surface": object()}, precip, cloud_layers, target_shape=(721, 1440))


def test_hazard_payload_uses_live_canonical_shape_over_coarse_precip(monkeypatch, tmp_path):
    svc = GFSService(str(tmp_path))
    coarse_shape = (241, 480)
    live_shape = (721, 1440)

    precip = np.ones(coarse_shape, dtype=float)
    cloud_layers = {
        "low": np.ones(live_shape, dtype=float) * 0.2,
        "mid": np.ones(live_shape, dtype=float) * 0.3,
        "high": np.ones(live_shape, dtype=float) * 0.8,
    }
    lat2d = np.zeros(coarse_shape, dtype=float)
    lon2d = np.zeros(coarse_shape, dtype=float)

    seen = {}

    def _hail(groups, p, c, **kwargs):
        seen["hail_precip_shape"] = np.asarray(p).shape
        seen["hail_target"] = kwargs.get("target_shape")
        return np.asarray(p) > 0.0

    def _ltg(groups, p, c, **kwargs):
        seen["ltg_precip_shape"] = np.asarray(p).shape
        seen["ltg_target"] = kwargs.get("target_shape")
        return np.asarray(p) > 0.0

    monkeypatch.setattr(svc, "threshold_to_mask", lambda arr, _: np.asarray(arr) > 0.1)
    monkeypatch.setattr(svc, "derive_hail_mask", _hail)
    monkeypatch.setattr(svc, "derive_lightning_mask", _ltg)
    monkeypatch.setattr(svc, "connected_components_or_simple_cell_polygons", lambda *args, **kwargs: [{"ok": True}])

    out = svc._derive_real_hazard_payloads(
        {},
        {
            "canonical_shape": live_shape,
            "precip": precip,
            "cloud_layers": cloud_layers,
            "lat2d": lat2d,
            "lon2d": lon2d,
        },
    )

    assert out["rain"]["count"] == 1
    assert seen["hail_target"] == live_shape
    assert seen["ltg_target"] == live_shape
    assert seen["hail_precip_shape"] == live_shape
    assert seen["ltg_precip_shape"] == live_shape


def test_hazard_canonical_shape_regression_guard(tmp_path):
    svc = GFSService(str(tmp_path))
    with pytest.raises(ValueError, match="hazard_canonical_shape_regression"):
        svc._select_hazard_canonical_shape(
            {"canonical_shape": (241, 480), "lat2d": np.zeros((721, 1440), dtype=float)},
            {"low": np.zeros((241, 480), dtype=float)},
            (241, 480),
        )


def test_create_quart_app_fails_fast_when_static_missing(monkeypatch, tmp_path):
    missing_static = tmp_path / "missing_static"
    monkeypatch.setattr(app_factory_module, "STATIC_DIR", missing_static)
    with pytest.raises(RuntimeError, match="static directory missing at startup"):
        app_factory_module.create_quart_app()
