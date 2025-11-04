from pathlib import Path
from services.renamer import compute_number_width, generate_preview_mappings


def test_compute_number_width():
    assert compute_number_width(0) == 3
    assert compute_number_width(1) == 3
    assert compute_number_width(9) == 3
    assert compute_number_width(10) == 3
    assert compute_number_width(999) == 3
    assert compute_number_width(1000) == 4


def test_generate_preview(tmp_path: Path):
    files = []
    for name in ["b.png", "a.jpg", "c.jpeg"]:
        p = tmp_path / name
        p.write_bytes(b"x")
        files.append(p)

    # 输入 files 前需按名称升序
    files.sort(key=lambda p: p.name)
    rows = generate_preview_mappings(tmp_path, files, prefix="Trip_")

    assert len(rows) == 3
    assert rows[0].new_path.name.startswith("Trip_001")
    assert rows[1].new_path.name.startswith("Trip_002")
    assert rows[2].new_path.name.startswith("Trip_003")
    assert all(r.status == "OK" for r in rows)


