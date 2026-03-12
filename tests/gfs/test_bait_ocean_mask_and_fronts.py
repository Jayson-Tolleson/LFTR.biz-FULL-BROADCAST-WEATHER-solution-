from server.gfs.derive.bait import derive_bait_payload
from server.gfs.polygon_builder import build_bait_ocean_field_v1


def test_build_bait_ocean_field_filters_non_ocean_cells():
    payload = build_bait_ocean_field_v1(
        bbox=[-10, 0, 10, 10],
        cell_size_deg=0.25,
        source_time=None,
        quality="coarse",
        ocean={
            "sst": [[20.0, float("nan")], [None, 41.0]],
            "chlorophyll": [[0.0, 0.0], [0.5, 0.0]],
            "current_u": [[0.0, 0.0], [0.0, 0.0]],
            "current_v": [[0.0, 0.0], [0.0, 0.0]],
            "optional_ssh_anomaly": [[0.0, 0.0], [0.0, 0.0]],
        },
        max_count=50,
    )

    assert payload["schema"] == "bait_ocean_field_v1"
    assert payload["count"] == 2
    assert payload["fields"]["sst"] == [20.0, 0.0]
    assert payload["fields"]["chlorophyll"] == [0.0, 0.5]


def test_derive_bait_payload_front_lines_are_local_and_ocean_derived():
    ocean = {
        "sst": [
            [10.0, 10.0, 10.0, 25.0, 25.0, 25.0],
            [10.0, 10.0, 10.0, 25.0, 25.0, 25.0],
            [10.0, 10.0, 10.0, 25.0, 25.0, 25.0],
            [10.0, 10.0, 10.0, 25.0, 25.0, 25.0],
            [10.0, 10.0, 10.0, 25.0, 25.0, 25.0],
            [10.0, 10.0, 10.0, 25.0, 25.0, 25.0],
        ]
    }
    atmospheric = {"cloud_total": [[40.0] * 6 for _ in range(6)], "precip_rate": [[0.1] * 6 for _ in range(6)], "wind_u": [[4.0] * 6 for _ in range(6)]}
    bbox = [-12.0, 20.0, -6.0, 26.0]

    bio = {"chlorophyll": [[0.6] * 6 for _ in range(6)]}
    payload = derive_bait_payload(atmospheric, ocean, bio, bbox=bbox)

    lines = payload["front_lines"]
    assert lines
    assert len(lines) <= 180

    bbox_width = bbox[2] - bbox[0]
    for line in lines:
        a, b = line["coordinates"]
        assert abs(a[0] - b[0]) < (bbox_width * 0.4)
        assert 0 <= line["score"] <= 1


def test_derive_bait_payload_emits_no_front_lines_without_sst():
    atmospheric = {"cloud_total": [[50.0, 50.0], [50.0, 50.0]], "precip_rate": [[0.0, 0.0], [0.0, 0.0]], "wind_u": [[5.0, 5.0], [5.0, 5.0]]}
    payload = derive_bait_payload(atmospheric, {}, {}, bbox=[-2.0, 0.0, 2.0, 2.0])
    assert payload["front_lines"] == []
