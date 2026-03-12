from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from typing import Any


log = logging.getLogger("server.gfs.derive.bait")


def _to_2d(field: Any) -> list[list[float]]:
    if not isinstance(field, list) or not field:
        return []
    if isinstance(field[0], list) and field[0] and isinstance(field[0][0], list):
        return field[0]
    if isinstance(field[0], list):
        return field
    return []


def _safe(v: Any, default: float = 0.0) -> float:
    try:
        n = float(v)
        if n != n or not math.isfinite(n):
            return default
        return n
    except Exception:
        return default


def _norm(value: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    out = (value - lo) / (hi - lo)
    return 0.0 if out < 0 else 1.0 if out > 1 else out


def _eastward_span(west: float, east: float) -> float:
    span = east - west
    if span < 0:
        span += 360.0
    return span


def _unwrap_lon_for_bbox(lon: float, west: float) -> float:
    out = lon
    while out < west:
        out += 360.0
    while out >= west + 360.0:
        out -= 360.0
    return out


def _normalize_lon(lon: float) -> float:
    out = ((lon + 180.0) % 360.0) - 180.0
    if out == -180.0:
        return 180.0
    return out


def _lon_from_fraction(j_frac: float, nx: int, bbox: list[float]) -> float:
    west, _, east, _ = bbox
    span = _eastward_span(west, east)
    lon_unwrapped = west + (j_frac / max(1, nx)) * span
    return _normalize_lon(lon_unwrapped)


def _lat_from_fraction(i_frac: float, ny: int, bbox: list[float]) -> float:
    _, south, _, north = bbox
    return south + (i_frac / max(1, ny)) * (north - south)


def _lat_lon(i: int, j: int, ny: int, nx: int, bbox: list[float]) -> tuple[float, float]:
    lat = _lat_from_fraction(i + 0.5, ny, bbox)
    lon = _lon_from_fraction(j + 0.5, nx, bbox)
    return lat, lon


def _is_ocean_valid(sst: float, chlorophyll: float) -> bool:
    if not math.isfinite(sst) or sst < -2.5 or sst > 38.5:
        return False
    if not math.isfinite(chlorophyll) or chlorophyll <= 0:
        return False
    return True


def _resample_nearest(grid: list[list[float]], ny: int, nx: int) -> list[list[float]]:
    src_ny = len(grid)
    src_nx = len(grid[0]) if src_ny else 0
    if src_ny == ny and src_nx == nx:
        return grid
    if src_ny < 1 or src_nx < 1 or ny < 1 or nx < 1:
        return []
    out: list[list[float]] = []
    for i in range(ny):
        si = min(src_ny - 1, round((i + 0.5) * src_ny / ny - 0.5))
        row: list[float] = []
        for j in range(nx):
            sj = min(src_nx - 1, round((j + 0.5) * src_nx / nx - 0.5))
            row.append(_safe(grid[si][sj], float("nan")))
        out.append(row)
    return out


def _edge_key(a: tuple[int, int], b: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]]:
    return (a, b) if a <= b else (b, a)


def _trace_component_polygon(component: set[tuple[int, int]], ny: int, nx: int, bbox: list[float]) -> list[list[float]]:
    undirected_count: dict[tuple[tuple[int, int], tuple[int, int]], int] = defaultdict(int)
    directed_edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()

    for i, j in component:
        edges = [
            ((j, i), (j + 1, i)),
            ((j + 1, i), (j + 1, i + 1)),
            ((j + 1, i + 1), (j, i + 1)),
            ((j, i + 1), (j, i)),
        ]
        for a, b in edges:
            k = _edge_key(a, b)
            undirected_count[k] += 1
            directed_edges.add((a, b))

    boundary_edges = {(a, b) for (a, b) in directed_edges if undirected_count[_edge_key(a, b)] == 1}
    if not boundary_edges:
        return []

    adjacency: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for a, b in boundary_edges:
        adjacency[a].append(b)

    start = min(adjacency.keys(), key=lambda p: (p[1], p[0]))
    ring_vertices = [start]
    current = start
    guard = max(16, len(boundary_edges) * 3)

    for _ in range(guard):
        options = adjacency.get(current) or []
        if not options:
            break
        nxt = min(options, key=lambda p: (p[1], p[0]))
        adjacency[current].remove(nxt)
        current = nxt
        if current == start:
            break
        ring_vertices.append(current)

    if len(ring_vertices) < 3:
        return []

    coords = [
        [
            _lon_from_fraction(float(vx), nx, bbox),
            _lat_from_fraction(float(vy), ny, bbox),
        ]
        for vx, vy in ring_vertices
    ]

    simplified: list[list[float]] = []
    for idx, p in enumerate(coords):
        prev_p = coords[idx - 1]
        next_p = coords[(idx + 1) % len(coords)]
        ax, ay = prev_p[0], prev_p[1]
        bx, by = p[0], p[1]
        cx, cy = next_p[0], next_p[1]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cross) < 1e-9:
            continue
        simplified.append([round(_normalize_lon(bx), 5), round(by, 5)])

    if len(simplified) < 3:
        return []

    for i in range(1, len(simplified)):
        if abs(simplified[i][0] - simplified[i - 1][0]) > 170:
            return []

    return simplified


def _connected_components(mask: list[list[bool]]) -> list[set[tuple[int, int]]]:
    ny = len(mask)
    nx = len(mask[0]) if ny else 0
    seen: set[tuple[int, int]] = set()
    out: list[set[tuple[int, int]]] = []

    for i in range(ny):
        for j in range(nx):
            if not mask[i][j] or (i, j) in seen:
                continue
            comp: set[tuple[int, int]] = set()
            q: deque[tuple[int, int]] = deque([(i, j)])
            seen.add((i, j))
            while q:
                ci, cj = q.popleft()
                comp.add((ci, cj))
                for ni, nj in ((ci - 1, cj), (ci + 1, cj), (ci, cj - 1), (ci, cj + 1)):
                    if 0 <= ni < ny and 0 <= nj < nx and mask[ni][nj] and (ni, nj) not in seen:
                        seen.add((ni, nj))
                        q.append((ni, nj))
            out.append(comp)
    return out


def _derive_front_lines_from_sst(sst_grid: list[list[float]], bbox: list[float], *, max_segments: int = 180) -> list[dict[str, Any]]:
    ny = len(sst_grid)
    nx = len(sst_grid[0]) if ny and isinstance(sst_grid[0], list) else 0
    if ny < 3 or nx < 3:
        return []

    _, south, _, north = bbox
    lat_step = (north - south) / max(1, ny)
    span_lon = _eastward_span(bbox[0], bbox[2])
    lon_step = span_lon / max(1, nx)
    sample_step = max(1, int(max(nx, ny) / 56))
    gradient_threshold = 0.35
    half_lat = abs(lat_step) * 1.2
    half_lon = abs(lon_step) * 1.2

    candidates: list[tuple[float, dict[str, Any]]] = []

    for i in range(1, ny - 1, sample_step):
        for j in range(1, nx - 1, sample_step):
            left = _safe(sst_grid[i][j - 1], float("nan"))
            right = _safe(sst_grid[i][j + 1], float("nan"))
            down = _safe(sst_grid[i - 1][j], float("nan"))
            up = _safe(sst_grid[i + 1][j], float("nan"))
            if not all(math.isfinite(v) for v in (left, right, down, up)):
                continue

            grad_x = (right - left) * 0.5
            grad_y = (up - down) * 0.5
            grad_mag = math.hypot(grad_x, grad_y)
            if grad_mag < gradient_threshold:
                continue

            lat_c, lon_c = _lat_lon(i, j, ny, nx, bbox)
            tangent_x = -grad_y
            tangent_y = grad_x
            tangent_norm = math.hypot(tangent_x, tangent_y)
            if tangent_norm <= 1e-9:
                continue
            tangent_x /= tangent_norm
            tangent_y /= tangent_norm

            lon_a = _normalize_lon(_unwrap_lon_for_bbox(lon_c, bbox[0]) - (tangent_x * half_lon))
            lon_b = _normalize_lon(_unwrap_lon_for_bbox(lon_c, bbox[0]) + (tangent_x * half_lon))
            dlat = tangent_y * half_lat
            segment = {
                "coordinates": [
                    [lon_a, lat_c - dlat],
                    [lon_b, lat_c + dlat],
                ],
                "score": round(min(1.0, grad_mag / 3.0), 3),
            }
            if abs(segment["coordinates"][0][0] - segment["coordinates"][1][0]) > 170:
                continue
            candidates.append((grad_mag, segment))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [segment for _, segment in candidates[:max_segments]]


def derive_bait_payload(atmospheric: dict[str, Any], ocean: dict[str, Any], bio: dict[str, Any], *, bbox: list[float] | None = None) -> dict[str, Any]:
    grid_cloud = _to_2d(atmospheric.get("cloud_total", atmospheric.get("Total_cloud_cover_entire_atmosphere", [])))
    grid_prate = _to_2d(atmospheric.get("precip_rate", atmospheric.get("Precipitation_rate_surface", [])))
    grid_wind_u = _to_2d(atmospheric.get("wind_u", []))
    grid_wind_v = _to_2d(atmospheric.get("wind_v", []))

    sst_raw = _to_2d(ocean.get("sst", []))
    current_u_raw = _to_2d(ocean.get("current_u", []))
    current_v_raw = _to_2d(ocean.get("current_v", []))
    chlorophyll_raw = _to_2d(bio.get("chlorophyll", []))

    if not bbox or len(bbox) < 4:
        return {
            "bait": {"status": "incomplete", "source": "suppressed_incomplete", "polygons": [], "meta": {"reason": "invalid_bbox"}},
            "bait_score": [],
            "front_lines": [],
            "convergence_polygons": [],
            "boil_probability_polygons": [],
            "confidence": {"overall": 0.0},
        }

    if not sst_raw or not chlorophyll_raw:
        return {
            "bait": {"status": "incomplete", "source": "suppressed_incomplete", "polygons": [], "meta": {"reason": "missing_ocean_or_bio"}},
            "bait_score": [],
            "front_lines": [],
            "convergence_polygons": [],
            "boil_probability_polygons": [],
            "confidence": {"overall": 0.0},
        }

    target_ny = min(len(sst_raw), len(chlorophyll_raw))
    target_nx = min(len(sst_raw[0]), len(chlorophyll_raw[0])) if target_ny else 0
    if target_ny < 1 or target_nx < 1:
        return {
            "bait": {"status": "incomplete", "source": "suppressed_incomplete", "polygons": [], "meta": {"reason": "empty_ocean_domain"}},
            "bait_score": [],
            "front_lines": [],
            "convergence_polygons": [],
            "boil_probability_polygons": [],
            "confidence": {"overall": 0.0},
        }

    sst = _resample_nearest(sst_raw, target_ny, target_nx)
    chlorophyll = _resample_nearest(chlorophyll_raw, target_ny, target_nx)
    current_u = _resample_nearest(current_u_raw, target_ny, target_nx) if current_u_raw else []
    current_v = _resample_nearest(current_v_raw, target_ny, target_nx) if current_v_raw else []

    bait_score: list[dict[str, Any]] = []
    zone_mask: list[list[bool]] = [[False for _ in range(target_nx)] for __ in range(target_ny)]
    score_grid: list[list[float]] = [[0.0 for _ in range(target_nx)] for __ in range(target_ny)]

    valid_cells = 0
    ocean_masked_cells = 0
    land_suppressed_cells = 0

    for i in range(target_ny):
        for j in range(target_nx):
            sst_v = _safe(sst[i][j], float("nan"))
            chl_v = _safe(chlorophyll[i][j], float("nan"))
            if not _is_ocean_valid(sst_v, chl_v):
                ocean_masked_cells += 1
                land_suppressed_cells += 1
                continue

            valid_cells += 1
            cloud_v = _safe(grid_cloud[i][j], 55.0) if i < len(grid_cloud) and j < len(grid_cloud[i]) else 55.0
            prate_v = _safe(grid_prate[i][j], 0.0) if i < len(grid_prate) and j < len(grid_prate[i]) else 0.0
            wu = _safe(grid_wind_u[i][j], 0.0) if i < len(grid_wind_u) and j < len(grid_wind_u[i]) else 0.0
            wv = _safe(grid_wind_v[i][j], 0.0) if i < len(grid_wind_v) and j < len(grid_wind_v[i]) else 0.0
            cu = _safe(current_u[i][j], 0.0) if i < len(current_u) and j < len(current_u[i]) else 0.0
            cv = _safe(current_v[i][j], 0.0) if i < len(current_v) and j < len(current_v[i]) else 0.0

            wind_score = 1.0 - abs(_norm(math.hypot(wu, wv), 0.0, 25.0) - 0.35)
            rain_score = 1.0 - _norm(prate_v, 0.0, 1.4)
            cloud_score = 1.0 - _norm(cloud_v, 10.0, 95.0)
            sst_score = 1.0 - abs(_norm(sst_v, 10.0, 30.0) - 0.5)
            chl_score = _norm(chl_v, 0.1, 2.4)
            current_score = _norm(math.hypot(cu, cv), 0.02, 1.2)

            score = (0.22 * wind_score) + (0.16 * rain_score) + (0.16 * cloud_score) + (0.24 * sst_score) + (0.16 * chl_score) + (0.06 * current_score)
            score = max(0.0, min(1.0, score))
            score_grid[i][j] = score
            zone_mask[i][j] = score >= 0.58
            lat, lon = _lat_lon(i, j, target_ny, target_nx, bbox)
            bait_score.append({"lat": lat, "lon": lon, "probability": round(score, 3)})

    components = _connected_components(zone_mask)
    zone_polygons: list[dict[str, Any]] = []
    suppressed_dateline = 0

    for comp in components:
        if len(comp) < 2:
            continue
        ring = _trace_component_polygon(comp, target_ny, target_nx, bbox)
        if len(ring) < 3:
            suppressed_dateline += 1
            continue
        prob = sum(score_grid[i][j] for i, j in comp) / max(1, len(comp))
        zone_polygons.append({"coordinates": ring, "probability": round(prob, 3)})

    fronts = _derive_front_lines_from_sst(sst, bbox) if valid_cells > 0 else []
    overall_conf = round(sum([b["probability"] for b in bait_score]) / max(1, len(bait_score)), 3) if bait_score else 0.0

    log.info(
        "bait merge diagnostics bbox=%s lat_orientation=%s lon_orientation=%s grid_shape=%sx%s masked=%s valid=%s components=%s polygons=%s",
        bbox,
        "south_to_north",
        "west_to_east_wrap",
        target_ny,
        target_nx,
        ocean_masked_cells,
        valid_cells,
        len(components),
        len(zone_polygons),
    )

    return {
        "bait": {
            "status": "ready" if valid_cells > 0 else "incomplete",
            "source": "full_stack" if valid_cells > 0 else "suppressed_incomplete",
            "polygons": zone_polygons,
            "meta": {
                "ocean_masked_cells": ocean_masked_cells,
                "land_suppressed_cells": land_suppressed_cells,
                "valid_cells": valid_cells,
                "suppressed_dateline_polygons": suppressed_dateline,
            },
        },
        "bait_score": bait_score,
        "front_lines": fronts,
        "convergence_polygons": zone_polygons,
        "boil_probability_polygons": [p for p in zone_polygons if p.get("probability", 0) >= 0.72],
        "confidence": {"overall": overall_conf},
    }
