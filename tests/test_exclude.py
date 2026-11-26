"""--exclude drops reviewed false positives (e.g. trees) and keeps every other ID unchanged."""

import csv

import pytest

from tankextract.pipeline import RunOptions, run
from tankextract.synthetic import EXPECTED, generate_scene


def _rows(path):
    return list(csv.DictReader(open(path, encoding="utf-8-sig")))


def test_exclude_keeps_other_ids(tmp_path):
    block = generate_scene(tmp_path / "scene")
    full = run(block, tmp_path / "full", RunOptions(reader="python", no_blender=True), log=lambda *a: None)
    assert len(full) == len(EXPECTED)
    names = {t.name: (round(t.center_x, 3), round(t.center_y, 3)) for t in full}
    drop = "Tank_002"
    kept = run(block, tmp_path / "cut", RunOptions(reader="python", no_blender=True, exclude=(drop,)), log=lambda *a: None)
    assert {t.name for t in kept} == set(names) - {drop}
    for t in kept:
        assert names[t.name] == (round(t.center_x, 3), round(t.center_y, 3))
    ids = [r["id"] for r in _rows(tmp_path / "cut" / "tanks.csv")]
    assert drop not in ids and len(ids) == len(EXPECTED) - 1
    for r in _rows(tmp_path / "cut" / "connectors.csv") if (tmp_path / "cut" / "connectors.csv").exists() else []:
        assert drop not in (r.get("tank_a"), r.get("tank_b"))
    for r in _rows(tmp_path / "cut" / "structures.csv") if (tmp_path / "cut" / "structures.csv").exists() else []:
        assert r.get("parent") != drop


def test_exclude_unknown_id_is_an_error(tmp_path):
    block = generate_scene(tmp_path / "scene")
    with pytest.raises(ValueError):
        run(block, tmp_path / "x", RunOptions(reader="python", no_blender=True, exclude=("Tank_999",)), log=lambda *a: None)
