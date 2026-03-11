from server.gfs.bbox import normalize_bbox, parse_bbox_param


def test_parse_bbox_valid():
    bbox = parse_bbox_param("-140,20,-100,50")
    assert bbox.west == -140
    assert bbox.north == 50


def test_bbox_clamp_and_stride_auto_selection():
    norm = normalize_bbox("-200,-95,220,95", pad=0.10, max_pad=0.5, max_cells=100)
    assert norm.merged.west >= -180
    assert norm.merged.east <= 180
    assert norm.stride > 1


def test_bbox_dateline_split():
    norm = normalize_bbox("170,-10,-170,10", pad=0.0, max_pad=0.5, max_cells=250000)
    assert len(norm.parts) == 2
