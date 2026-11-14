"""Offline CLI surface tests: dry-run and argument handling never touch the network."""

from __future__ import annotations

import ingest_copernicus


def test_dry_run_prints_all_resolved_tiles_without_writing(tmp_path, capsys):
    manifest = tmp_path / "should-not-exist.json"
    code = ingest_copernicus.main(
        ["--dry-run", "--cache-dir", str(tmp_path / "cache"), "--manifest", str(manifest)]
    )
    output = capsys.readouterr().out
    assert code == 0
    assert "DRY RUN" in output
    for tile in (
        "Copernicus_DSM_COG_30_S24_00_W069_00_DEM",
        "Copernicus_DSM_COG_30_N36_00_W118_00_DEM",
        "Copernicus_DSM_COG_30_N29_00_W014_00_DEM",
        "Copernicus_DSM_COG_30_N75_00_W090_00_DEM",
        "Copernicus_DSM_COG_30_N37_00_W007_00_DEM",
    ):
        assert tile in output
    assert not manifest.exists()
    assert not (tmp_path / "cache").exists()


def test_only_filters_dry_run_to_one_candidate(tmp_path, capsys):
    code = ingest_copernicus.main(
        ["--dry-run", "--only", "earth-rio-tinto", "--cache-dir", str(tmp_path)]
    )
    output = capsys.readouterr().out
    assert code == 0
    assert "earth-rio-tinto" in output
    assert "earth-atacama-desert" not in output


def test_unknown_only_slug_fails_with_clear_message(capsys):
    code = ingest_copernicus.main(["--dry-run", "--only", "not-a-real-place"])
    error = capsys.readouterr().err
    assert code == 2
    assert "unknown candidate" in error


def test_retry_cap_is_enforced(capsys):
    code = ingest_copernicus.main(["--dry-run", "--max-retries", "3"])
    error = capsys.readouterr().err
    assert code == 2
    assert "between 0 and 2" in error
