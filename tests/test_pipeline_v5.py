from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from pipeline_v5 import (
    generate_source_file,
    process_source_file,
    validate_and_normalize_source,
)


def test_validation_rejects_invalid_and_duplicate_rows() -> None:
    source = pd.DataFrame(
        {
            "Identifiant": [1, 2, 3, 4, 5, 6, 7],
            "Coord X": [0, 1, 0, 0, 0, 0, "erreur"],
            "Coord Y": [0, 0, 1, 0, 0, 0, 2],
            "Coord Z": [0, 0, 0, 1, 0, 0, 3],
            "Nom": ["a", "b", "c", "d", "origine bis", "origine ter", "invalide"],
        }
    )
    result = validate_and_normalize_source(
        source,
        source_name="test",
        x_column="Coord X",
        y_column="Coord Y",
        z_column="Coord Z",
        id_column="Identifiant",
        label_column="Nom",
    )
    assert len(result.clean_df) == 4
    assert len(result.rejected_df) == 3
    assert set(result.clean_df["point_id"]) == {1, 2, 3, 4}


def test_end_to_end_excel_pipeline(tmp_path: Path) -> None:
    input_path = tmp_path / "points_test.xlsx"
    generate_source_file(
        output_path=input_path,
        point_count=100,
        distribution="volume",
        seed=42,
    )

    result = process_source_file(
        input_path=input_path,
        runs_dir=tmp_path / "runs",
        sheet="Points",
        engine_name="historical",
        record_every=25,
        progress_every=1000,
        scipy_reference_max_points=500,
        full_convexity_max_vertices=500,
        show=False,
        create_screenshot=False,
    )

    assert result.state.points_processed == 100
    assert result.state.volume > 0
    assert result.state.area > 0
    assert all(item.passed for item in result.validation_items)
    assert (result.run_dir / "report.md").exists()
    assert (result.run_dir / "metrics.json").exists()
    assert (result.run_dir / "exports" / "results.xlsx").exists()
    assert (result.run_dir / "exports" / "final_hull.vtp").exists()

    connection = duckdb.connect(str(result.database_path), read_only=True)
    try:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        assert {
            "source_points",
            "clean_points",
            "rejected_points",
            "hull_vertices",
            "hull_faces",
            "hull_steps",
            "validation_results",
            "run_info",
        }.issubset(tables)
        source_count = connection.execute("SELECT COUNT(*) FROM source_points").fetchone()[0]
        assert source_count == 100
    finally:
        connection.close()


def test_generated_sphere_keeps_all_points(tmp_path: Path) -> None:
    input_path = tmp_path / "sphere.csv"
    generate_source_file(
        output_path=input_path,
        point_count=80,
        distribution="sphere",
        seed=7,
    )
    result = process_source_file(
        input_path=input_path,
        runs_dir=tmp_path / "runs",
        engine_name="historical",
        record_every=40,
        progress_every=1000,
        show=False,
        create_screenshot=False,
    )
    assert len(result.state.points) == 80
    assert len(result.state.faces) == 2 * 80 - 4
    radii = np.linalg.norm(result.state.points, axis=1)
    assert np.allclose(radii, 1.0)
