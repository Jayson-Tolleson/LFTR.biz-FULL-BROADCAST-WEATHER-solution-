from __future__ import annotations

from pathlib import Path

from server.gfs_service import GFSService


def test_gfs_routes_reference_existing_service_methods(tmp_path):
    src = Path('server/gfs/routes.py').read_text(encoding='utf-8')
    referenced = {
        'health',
        'config',
        'get_scene_payload',
        'status_payload',
        'cloud_tiles_payload',
        'hazards_payload',
        'diagnostics_payload',
        'fish_payload',
        'layer_tile_payload',
        'tile_aggregate_payload',
        'tile_diagnostics_payload',
        'location_media',
        'upsert_report',
        'upsert_live',
        'save_upload_video',
        'frame_payload',
        'overlay_payload',
        'contours_payload',
        'legend_payload',
        'tile_png_bytes',
    }
    svc = GFSService(str(tmp_path))
    for name in referenced:
        assert hasattr(svc, name), f"GFSService missing method referenced by routes: {name}"
    # lightweight guard that routes are still calling these names
    for name in referenced:
        if name in {'save_upload_video'}:
            continue
        assert f'gfs.{name}(' in src


def test_gfs_wrapper_payload_methods_are_json_safe(tmp_path):
    svc = GFSService(str(tmp_path))
    assert isinstance(svc.health_payload(), dict)
    assert isinstance(svc.config_payload(), dict)
    assert isinstance(svc.status_payload(), dict)
    assert isinstance(svc.diagnostics_payload(), dict)
    hazards = svc.hazards_payload()
    assert isinstance(hazards, dict)
    assert {'rain', 'hail', 'lightning'}.issubset(hazards.keys())


def test_tile_aggregate_payload_exposes_expected_layers(monkeypatch, tmp_path):
    svc = GFSService(str(tmp_path))

    def fake_layer(layer: str, z: int, x: int, y: int, pad_deg: float = 0.18, debug: bool = False):
        return {'features': [{'layer': layer, 'z': z, 'x': x, 'y': y}]}

    monkeypatch.setattr(svc, 'layer_tile_payload', fake_layer)
    payload = svc.tile_aggregate_payload(z=3, x=1, y=2, debug=False)
    assert payload['status']['ok'] is True
    assert payload['clouds'] and payload['clouds'][0]['layer'] == 'clouds'
    assert payload['rain'] and payload['rain'][0]['layer'] == 'precip'
    assert payload['hail'] and payload['hail'][0]['layer'] == 'hail'
    assert payload['lightning'] and payload['lightning'][0]['layer'] == 'lightning'
