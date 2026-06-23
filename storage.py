from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import duckdb
import numpy as np
import pandas as pd

DistributionName = Literal["volume", "sphere", "cube"]


@dataclass(frozen=True, slots=True)
class PointRecord:
    """Représente un point scientifique identifié dans la base DuckDB."""

    point_id: int
    x: float
    y: float
    z: float
    label: str

    @property
    def coordinates(self) -> np.ndarray:
        """Retourne les coordonnées sous une forme directement utilisable par NumPy."""

        return np.array([self.x, self.y, self.z], dtype=np.float64)


DEMO_POINTS: tuple[PointRecord, ...] = (
    PointRecord(1, 1.0, 1.0, 1.0, "Sommet initial A"),
    PointRecord(2, -1.0, -1.0, 1.0, "Sommet initial B"),
    PointRecord(3, -1.0, 1.0, -1.0, "Sommet initial C"),
    PointRecord(4, 1.0, -1.0, -1.0, "Sommet initial D"),
    PointRecord(5, 0.0, 0.0, 0.0, "Point intérieur"),
    PointRecord(6, 0.25, -0.10, 0.15, "Point intérieur"),
    PointRecord(7, 1.80, 0.10, 0.00, "Extension +X"),
    PointRecord(8, -1.70, -0.20, 0.10, "Extension -X"),
    PointRecord(9, 0.10, 1.90, 0.00, "Extension +Y"),
    PointRecord(10, -0.10, -1.80, 0.00, "Extension -Y"),
    PointRecord(11, 0.00, 0.10, 2.00, "Extension +Z"),
    PointRecord(12, 0.00, -0.10, -2.10, "Extension -Z"),
    PointRecord(13, 1.40, 1.30, 0.20, "Diagonale XY"),
    PointRecord(14, -1.30, 1.40, -0.10, "Diagonale XY"),
    PointRecord(15, -1.40, -1.20, 0.30, "Diagonale XY"),
    PointRecord(16, 1.30, -1.40, -0.20, "Diagonale XY"),
    PointRecord(17, 0.90, 0.80, 1.40, "Diagonale haute"),
    PointRecord(18, -0.80, 0.90, 1.30, "Diagonale haute"),
    PointRecord(19, -0.90, -0.80, -1.50, "Diagonale basse"),
    PointRecord(20, 0.80, -0.90, -1.40, "Diagonale basse"),
)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS points (
    point_id BIGINT PRIMARY KEY,
    x DOUBLE NOT NULL,
    y DOUBLE NOT NULL,
    z DOUBLE NOT NULL,
    label VARCHAR NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS hull_runs (
    run_id VARCHAR PRIMARY KEY,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    status VARCHAR NOT NULL,
    points_total BIGINT NOT NULL,
    points_processed BIGINT NOT NULL DEFAULT 0,
    hull_vertex_count BIGINT NOT NULL DEFAULT 0,
    hull_face_count BIGINT NOT NULL DEFAULT 0,
    surface_area DOUBLE,
    volume DOUBLE,
    engine VARCHAR NOT NULL DEFAULT 'scipy_rebuild'
);

CREATE TABLE IF NOT EXISTS hull_steps (
    run_id VARCHAR NOT NULL,
    step_number BIGINT NOT NULL,
    inserted_point_id BIGINT,
    point_retained BOOLEAN NOT NULL,
    hull_vertex_count BIGINT NOT NULL,
    hull_face_count BIGINT NOT NULL,
    surface_area DOUBLE NOT NULL,
    volume DOUBLE NOT NULL,
    added_face_count BIGINT NOT NULL,
    removed_face_count BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, step_number)
);

CREATE TABLE IF NOT EXISTS benchmark_results (
    benchmark_id VARCHAR PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    distribution VARCHAR NOT NULL,
    requested_points BIGINT NOT NULL,
    processed_points BIGINT NOT NULL,
    seed BIGINT NOT NULL,
    completed BOOLEAN NOT NULL,
    generation_seconds DOUBLE NOT NULL,
    database_write_seconds DOUBLE NOT NULL,
    database_read_seconds DOUBLE NOT NULL,
    hull_seconds DOUBLE NOT NULL,
    total_seconds DOUBLE NOT NULL,
    points_per_second DOUBLE NOT NULL,
    hull_vertex_count BIGINT NOT NULL,
    hull_face_count BIGINT NOT NULL,
    surface_area DOUBLE NOT NULL,
    volume DOUBLE NOT NULL,
    engine VARCHAR NOT NULL DEFAULT 'scipy_rebuild',
    reference_valid BOOLEAN,
    reference_volume_error DOUBLE,
    reference_area_error DOUBLE,
    speedup_vs_scipy DOUBLE
);
"""


def connect_database(database_path: str | Path) -> duckdb.DuckDBPyConnection:
    """Ouvre la base et crée son dossier parent si nécessaire."""

    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(path))
    connection.execute(SCHEMA_SQL)
    connection.execute(
        "ALTER TABLE hull_runs ADD COLUMN IF NOT EXISTS engine VARCHAR DEFAULT 'scipy_rebuild'"
    )
    connection.execute(
        "ALTER TABLE benchmark_results ADD COLUMN IF NOT EXISTS engine VARCHAR DEFAULT 'scipy_rebuild'"
    )
    connection.execute(
        "ALTER TABLE benchmark_results ADD COLUMN IF NOT EXISTS reference_valid BOOLEAN"
    )
    connection.execute(
        "ALTER TABLE benchmark_results ADD COLUMN IF NOT EXISTS reference_volume_error DOUBLE"
    )
    connection.execute(
        "ALTER TABLE benchmark_results ADD COLUMN IF NOT EXISTS reference_area_error DOUBLE"
    )
    connection.execute(
        "ALTER TABLE benchmark_results ADD COLUMN IF NOT EXISTS speedup_vs_scipy DOUBLE"
    )
    connection.execute(
        "UPDATE hull_runs SET engine = 'scipy_rebuild' WHERE engine IS NULL"
    )
    connection.execute(
        "UPDATE benchmark_results SET engine = 'scipy_rebuild' WHERE engine IS NULL"
    )
    return connection


def seed_demo_points(
    connection: duckdb.DuckDBPyConnection,
    *,
    reset: bool = False,
) -> int:
    """Insère les vingt points reproductibles du prototype."""

    if reset:
        clear_active_dataset(connection)

    existing_count = connection.execute("SELECT COUNT(*) FROM points").fetchone()[0]
    if existing_count:
        return int(existing_count)

    return replace_points(connection, DEMO_POINTS, clear_run_history=False)


def generate_points(
    count: int,
    *,
    distribution: DistributionName,
    seed: int,
) -> list[PointRecord]:
    """Crée un nuage 3D reproductible pour mesurer les limites du prototype."""

    if count < 4:
        raise ValueError("Au moins quatre points sont nécessaires.")

    rng = np.random.default_rng(seed)
    if distribution == "volume":
        directions = rng.normal(size=(count, 3))
        directions /= np.linalg.norm(directions, axis=1, keepdims=True)
        radii = np.cbrt(rng.random(count))
        coordinates = directions * radii[:, None]
    elif distribution == "sphere":
        coordinates = rng.normal(size=(count, 3))
        coordinates /= np.linalg.norm(coordinates, axis=1, keepdims=True)
    elif distribution == "cube":
        coordinates = rng.uniform(-1.0, 1.0, size=(count, 3))
    else:
        raise ValueError(f"Distribution inconnue : {distribution}")

    return [
        PointRecord(
            point_id=index + 1,
            x=float(row[0]),
            y=float(row[1]),
            z=float(row[2]),
            label=f"{distribution}_{index + 1:07d}",
        )
        for index, row in enumerate(coordinates)
    ]


def clear_active_dataset(connection: duckdb.DuckDBPyConnection) -> None:
    """Efface le nuage actif et ses exécutions sans supprimer l'historique des benchmarks."""

    connection.execute("DELETE FROM hull_steps")
    connection.execute("DELETE FROM hull_runs")
    connection.execute("DELETE FROM points")


def load_points(connection: duckdb.DuckDBPyConnection) -> list[PointRecord]:
    """Charge les points dans leur ordre d'injection déterministe."""

    rows = connection.execute(
        """
        SELECT point_id, x, y, z, label
        FROM points
        ORDER BY point_id
        """
    ).fetchall()
    return [PointRecord(*row) for row in rows]


def start_run(
    connection: duckdb.DuckDBPyConnection,
    run_id: str,
    points_total: int,
    engine: str,
) -> None:
    """Crée la ligne d'audit d'une nouvelle exécution."""

    connection.execute(
        """
        INSERT INTO hull_runs (
            run_id, status, points_total, points_processed, engine
        ) VALUES (?, 'running', ?, 0, ?)
        """,
        [run_id, points_total, engine],
    )


def record_step(
    connection: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    step_number: int,
    inserted_point_id: int | None,
    point_retained: bool,
    hull_vertex_count: int,
    hull_face_count: int,
    surface_area: float,
    volume: float,
    added_face_count: int,
    removed_face_count: int,
) -> None:
    """Enregistre les métriques d'une étape sans stocker le maillage complet."""

    connection.execute(
        """
        INSERT INTO hull_steps (
            run_id,
            step_number,
            inserted_point_id,
            point_retained,
            hull_vertex_count,
            hull_face_count,
            surface_area,
            volume,
            added_face_count,
            removed_face_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run_id,
            step_number,
            inserted_point_id,
            point_retained,
            hull_vertex_count,
            hull_face_count,
            surface_area,
            volume,
            added_face_count,
            removed_face_count,
        ],
    )


def finish_run(
    connection: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    points_processed: int,
    hull_vertex_count: int,
    hull_face_count: int,
    surface_area: float,
    volume: float,
    status: str = "completed",
) -> None:
    """Clôture l'exécution et conserve ses métriques finales."""

    connection.execute(
        """
        UPDATE hull_runs
        SET
            finished_at = CURRENT_TIMESTAMP,
            status = ?,
            points_processed = ?,
            hull_vertex_count = ?,
            hull_face_count = ?,
            surface_area = ?,
            volume = ?
        WHERE run_id = ?
        """,
        [
            status,
            points_processed,
            hull_vertex_count,
            hull_face_count,
            surface_area,
            volume,
            run_id,
        ],
    )


def replace_points(
    connection: duckdb.DuckDBPyConnection,
    points: Iterable[PointRecord],
    *,
    clear_run_history: bool = True,
) -> int:
    """Remplace le nuage actif par un jeu de données matérialisé."""

    materialized = list(points)
    frame = pd.DataFrame(
        {
            "point_id": [point.point_id for point in materialized],
            "x": [point.x for point in materialized],
            "y": [point.y for point in materialized],
            "z": [point.z for point in materialized],
            "label": [point.label for point in materialized],
        }
    )

    # DuckDB lit directement le DataFrame en colonnes. Cette insertion vectorisée
    # évite les milliers d'appels Python produits par executemany.
    connection.register("point_batch", frame)
    connection.execute("BEGIN TRANSACTION")
    try:
        if clear_run_history:
            clear_active_dataset(connection)
        else:
            connection.execute("DELETE FROM points")

        connection.execute(
            """
            INSERT INTO points (point_id, x, y, z, label)
            SELECT point_id, x, y, z, label
            FROM point_batch
            """
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.unregister("point_batch")
    return len(materialized)


def record_benchmark(
    connection: duckdb.DuckDBPyConnection,
    *,
    benchmark_id: str,
    distribution: str,
    engine: str,
    requested_points: int,
    processed_points: int,
    seed: int,
    completed: bool,
    generation_seconds: float,
    database_write_seconds: float,
    database_read_seconds: float,
    hull_seconds: float,
    total_seconds: float,
    hull_vertex_count: int,
    hull_face_count: int,
    surface_area: float,
    volume: float,
    reference_valid: bool | None,
    reference_volume_error: float | None,
    reference_area_error: float | None,
    speedup_vs_scipy: float | None,
) -> None:
    """Conserve une mesure de performance comparable entre plusieurs tailles de nuage."""

    points_per_second = processed_points / hull_seconds if hull_seconds > 0 else 0.0
    connection.execute(
        """
        INSERT INTO benchmark_results (
            benchmark_id,
            distribution,
            engine,
            requested_points,
            processed_points,
            seed,
            completed,
            generation_seconds,
            database_write_seconds,
            database_read_seconds,
            hull_seconds,
            total_seconds,
            points_per_second,
            hull_vertex_count,
            hull_face_count,
            surface_area,
            volume,
            reference_valid,
            reference_volume_error,
            reference_area_error,
            speedup_vs_scipy
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            benchmark_id,
            distribution,
            engine,
            requested_points,
            processed_points,
            seed,
            completed,
            generation_seconds,
            database_write_seconds,
            database_read_seconds,
            hull_seconds,
            total_seconds,
            points_per_second,
            hull_vertex_count,
            hull_face_count,
            surface_area,
            volume,
            reference_valid,
            reference_volume_error,
            reference_area_error,
            speedup_vs_scipy,
        ],
    )
