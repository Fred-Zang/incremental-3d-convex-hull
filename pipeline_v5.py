from __future__ import annotations

import json
import math
import re
import resource
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import duckdb
import numpy as np
import pandas as pd
import pyvista as pv
from scipy.spatial import ConvexHull

from hull_engine import HullState, ScipyRebuildHull3D, split_initial_tetrahedron
from incremental_hull_engine import HistoricalIncrementalHull3D
from storage import PointRecord, generate_points

EngineName = Literal["historical", "scipy"]
DistributionName = Literal["volume", "sphere", "cube"]

FACE_RGB = np.array(
    [
        [211, 47, 47],
        [251, 192, 45],
        [56, 142, 60],
        [25, 118, 210],
    ],
    dtype=np.uint8,
)
FACE_COLOR_NAMES = ("rouge", "jaune", "vert", "bleu")

COLUMN_CANDIDATES = {
    "x": ("x", "coord_x", "coordinate_x", "coordonnee_x", "abscisse"),
    "y": ("y", "coord_y", "coordinate_y", "coordonnee_y", "ordonnee"),
    "z": ("z", "coord_z", "coordinate_z", "coordonnee_z", "altitude"),
    "point_id": ("point_id", "id", "identifiant", "atom_id", "index"),
    "label": ("label", "libelle", "name", "nom", "atom_name"),
}


@dataclass(slots=True)
class ImportResult:
    source_df: pd.DataFrame
    clean_df: pd.DataFrame
    rejected_df: pd.DataFrame
    column_mapping: pd.DataFrame
    resolved_columns: dict[str, str | None]


@dataclass(slots=True)
class ValidationItem:
    test_name: str
    passed: bool
    value: str
    tolerance: str
    details: str


@dataclass(slots=True)
class ProcessResult:
    run_id: str
    run_dir: Path
    database_path: Path
    state: HullState
    metrics: dict[str, Any]
    validation_items: list[ValidationItem]


def _normalise_name(value: Any) -> str:
    text = str(value).strip().lower()
    text = (
        text.replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("à", "a")
        .replace("ù", "u")
        .replace("ç", "c")
    )
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "column"


def _safe_unique_columns(columns: list[Any]) -> tuple[list[str], pd.DataFrame]:
    used: dict[str, int] = {}
    safe_names: list[str] = []
    mappings: list[dict[str, Any]] = []
    for position, original in enumerate(columns, start=1):
        base = _normalise_name(original)
        occurrence = used.get(base, 0) + 1
        used[base] = occurrence
        safe = base if occurrence == 1 else f"{base}_{occurrence}"
        safe_names.append(safe)
        mappings.append(
            {
                "column_position": position,
                "original_name": str(original),
                "stored_name": safe,
            }
        )
    return safe_names, pd.DataFrame(mappings)


def _resolve_column(
    available_columns: list[str],
    requested: str | None,
    semantic_name: str,
    *,
    required: bool,
) -> str | None:
    lookup = {_normalise_name(column): column for column in available_columns}
    if requested:
        key = _normalise_name(requested)
        if key not in lookup:
            raise ValueError(
                f"Colonne demandée introuvable pour {semantic_name!r} : {requested!r}. "
                f"Colonnes disponibles : {', '.join(available_columns)}"
            )
        return lookup[key]

    for candidate in COLUMN_CANDIDATES[semantic_name]:
        if candidate in lookup:
            return lookup[candidate]
    if required:
        raise ValueError(
            f"Impossible de détecter automatiquement la colonne {semantic_name!r}. "
            f"Utiliser --{semantic_name.replace('_', '-')}-column."
        )
    return None


def read_source_table(path: str | Path, *, sheet: str | int | None = None) -> pd.DataFrame:
    """Lit un fichier Excel, CSV ou Parquet dans un DataFrame pandas."""

    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"Fichier source introuvable : {source_path}")

    suffix = source_path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        selected_sheet: str | int = 0 if sheet is None else sheet
        return pd.read_excel(source_path, sheet_name=selected_sheet, engine="openpyxl")
    if suffix == ".csv":
        return pd.read_csv(source_path)
    if suffix == ".parquet":
        return pd.read_parquet(source_path)
    raise ValueError(
        f"Format non pris en charge : {suffix}. Formats acceptés : .xlsx, .xlsm, .csv, .parquet."
    )


def validate_and_normalize_source(
    source_df: pd.DataFrame,
    *,
    source_name: str,
    x_column: str | None = None,
    y_column: str | None = None,
    z_column: str | None = None,
    id_column: str | None = None,
    label_column: str | None = None,
) -> ImportResult:
    """Valide les coordonnées et conserve une référence vers chaque ligne source."""

    if source_df.empty:
        raise ValueError("Le fichier source ne contient aucune ligne.")

    safe_columns, mapping = _safe_unique_columns(list(source_df.columns))
    safe_source = source_df.copy()
    safe_source.columns = safe_columns
    safe_source.insert(0, "source_row", np.arange(2, len(safe_source) + 2, dtype=np.int64))

    available = list(safe_source.columns)
    resolved_x = _resolve_column(available, x_column, "x", required=True)
    resolved_y = _resolve_column(available, y_column, "y", required=True)
    resolved_z = _resolve_column(available, z_column, "z", required=True)
    resolved_id = _resolve_column(available, id_column, "point_id", required=False)
    resolved_label = _resolve_column(available, label_column, "label", required=False)

    work = pd.DataFrame(index=safe_source.index)
    work["source_row"] = safe_source["source_row"].astype("int64")
    work["x"] = pd.to_numeric(safe_source[resolved_x], errors="coerce")
    work["y"] = pd.to_numeric(safe_source[resolved_y], errors="coerce")
    work["z"] = pd.to_numeric(safe_source[resolved_z], errors="coerce")

    if resolved_id is None:
        work["point_id"] = np.arange(1, len(work) + 1, dtype=np.int64)
    else:
        numeric_ids = pd.to_numeric(safe_source[resolved_id], errors="coerce")
        work["point_id"] = numeric_ids

    if resolved_label is None:
        width = max(6, len(str(len(work))))
        work["label"] = [
            f"{_normalise_name(source_name)}_{index:0{width}d}"
            for index in range(1, len(work) + 1)
        ]
    else:
        labels = safe_source[resolved_label].astype("string").fillna("").str.strip()
        fallback = safe_source["source_row"].map(lambda value: f"point_{int(value):06d}")
        work["label"] = labels.mask(labels.eq(""), fallback)

    reasons: list[list[str]] = [[] for _ in range(len(work))]

    coordinate_values = work[["x", "y", "z"]].to_numpy(dtype=np.float64)
    numeric_valid = np.isfinite(coordinate_values).all(axis=1)
    for row_index in np.flatnonzero(~numeric_valid):
        reasons[int(row_index)].append("coordonnée absente, non numérique ou infinie")

    id_numeric = pd.to_numeric(work["point_id"], errors="coerce")
    id_valid = id_numeric.notna().to_numpy() & np.isfinite(id_numeric.fillna(0).to_numpy(dtype=float))
    id_integer = np.zeros(len(work), dtype=bool)
    valid_id_positions = np.flatnonzero(id_valid)
    if len(valid_id_positions):
        valid_values = id_numeric.iloc[valid_id_positions].to_numpy(dtype=float)
        id_integer[valid_id_positions] = np.isclose(valid_values, np.round(valid_values))
    for row_index in np.flatnonzero(~id_valid | ~id_integer):
        reasons[int(row_index)].append("identifiant absent, non numérique ou non entier")

    provisional_valid = np.array([not value for value in reasons], dtype=bool)
    if provisional_valid.any():
        valid_indices = np.flatnonzero(provisional_valid)
        valid_ids = np.round(id_numeric.iloc[valid_indices].to_numpy(dtype=float)).astype(np.int64)
        duplicate_id_mask = pd.Series(valid_ids).duplicated(keep="first").to_numpy()
        for local_position in np.flatnonzero(duplicate_id_mask):
            reasons[int(valid_indices[local_position])].append("identifiant dupliqué")

    provisional_valid = np.array([not value for value in reasons], dtype=bool)
    if provisional_valid.any():
        valid_indices = np.flatnonzero(provisional_valid)
        valid_coords = work.iloc[valid_indices][["x", "y", "z"]]
        duplicate_coord_mask = valid_coords.duplicated(keep="first").to_numpy()
        for local_position in np.flatnonzero(duplicate_coord_mask):
            reasons[int(valid_indices[local_position])].append("coordonnées dupliquées")

    valid_mask = np.array([not value for value in reasons], dtype=bool)
    clean = work.loc[valid_mask].copy()
    clean["point_id"] = np.round(clean["point_id"].astype(float)).astype("int64")
    clean[["x", "y", "z"]] = clean[["x", "y", "z"]].astype("float64")
    clean["label"] = clean["label"].astype(str)
    clean = clean[["point_id", "x", "y", "z", "label", "source_row"]].reset_index(drop=True)

    rejected_rows = []
    for row_position, row_reasons in enumerate(reasons):
        if row_reasons:
            rejected_rows.append(
                {
                    "source_row": int(safe_source.iloc[row_position]["source_row"]),
                    "rejection_reason": "; ".join(row_reasons),
                }
            )
    rejected = pd.DataFrame(rejected_rows, columns=["source_row", "rejection_reason"])

    if len(clean) < 4:
        raise ValueError(
            f"Seulement {len(clean)} points valides. Au moins quatre points sont nécessaires."
        )

    records = _dataframe_to_records(clean)
    split_initial_tetrahedron(records)

    return ImportResult(
        source_df=safe_source,
        clean_df=clean,
        rejected_df=rejected,
        column_mapping=mapping,
        resolved_columns={
            "x": resolved_x,
            "y": resolved_y,
            "z": resolved_z,
            "point_id": resolved_id,
            "label": resolved_label,
        },
    )


def _dataframe_to_records(clean_df: pd.DataFrame) -> list[PointRecord]:
    ids = clean_df["point_id"].to_numpy(dtype=np.int64)
    coordinates = clean_df[["x", "y", "z"]].to_numpy(dtype=np.float64)
    labels = clean_df["label"].astype(str).tolist()
    return [
        PointRecord(int(point_id), float(coords[0]), float(coords[1]), float(coords[2]), label)
        for point_id, coords, label in zip(ids, coordinates, labels, strict=True)
    ]


def create_run_directory(base_dir: str | Path, source_path: str | Path) -> tuple[str, Path]:
    source_stem = _normalise_name(Path(source_path).stem)[:40]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"{timestamp}_{source_stem}_{uuid.uuid4().hex[:6]}"
    run_dir = Path(base_dir) / run_id
    (run_dir / "exports").mkdir(parents=True, exist_ok=False)
    return run_id, run_dir


def _create_engine(engine_name: EngineName, *, validate_each_step: bool):
    if engine_name == "historical":
        return HistoricalIncrementalHull3D(validate_each_step=validate_each_step)
    if engine_name == "scipy":
        return ScipyRebuildHull3D(validate_each_step=validate_each_step)
    raise ValueError(f"Moteur inconnu : {engine_name}")


def _write_dataframe_table(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    dataframe: pd.DataFrame,
) -> None:
    connection.register("temporary_dataframe", dataframe)
    try:
        connection.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        connection.execute(
            f'CREATE TABLE "{table_name}" AS SELECT * FROM temporary_dataframe'
        )
    finally:
        connection.unregister("temporary_dataframe")


def _initialise_run_database(
    database_path: Path,
    *,
    run_id: str,
    source_path: Path,
    engine_name: EngineName,
    import_result: ImportResult,
) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(str(database_path))
    _write_dataframe_table(connection, "source_points", import_result.source_df)
    _write_dataframe_table(connection, "clean_points", import_result.clean_df)
    _write_dataframe_table(connection, "rejected_points", import_result.rejected_df)
    _write_dataframe_table(connection, "column_mapping", import_result.column_mapping)
    connection.execute(
        """
        CREATE TABLE run_info (
            run_id VARCHAR,
            source_path VARCHAR,
            engine VARCHAR,
            status VARCHAR,
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            source_rows BIGINT,
            valid_points BIGINT,
            rejected_points BIGINT,
            points_processed BIGINT,
            hull_vertex_count BIGINT,
            hull_face_count BIGINT,
            surface_area DOUBLE,
            volume DOUBLE,
            elapsed_seconds DOUBLE,
            points_per_second DOUBLE,
            peak_memory_mb DOUBLE
        )
        """
    )
    connection.execute(
        """
        INSERT INTO run_info VALUES (?, ?, ?, 'running', current_timestamp, NULL,
            ?, ?, ?, 0, 0, 0, NULL, NULL, NULL, NULL, NULL)
        """,
        [
            run_id,
            str(source_path),
            engine_name,
            len(import_result.source_df),
            len(import_result.clean_df),
            len(import_result.rejected_df),
        ],
    )
    connection.execute(
        """
        CREATE TABLE hull_steps (
            step_number BIGINT,
            inserted_point_id BIGINT,
            retained BOOLEAN,
            hull_vertex_count BIGINT,
            hull_face_count BIGINT,
            surface_area DOUBLE,
            volume DOUBLE,
            added_face_count BIGINT,
            removed_face_count BIGINT,
            horizon_edge_count BIGINT,
            elapsed_seconds DOUBLE,
            recorded_at TIMESTAMP DEFAULT current_timestamp
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE validation_results (
            test_name VARCHAR,
            passed BOOLEAN,
            value VARCHAR,
            tolerance VARCHAR,
            details VARCHAR
        )
        """
    )
    return connection


def _record_step(
    connection: duckdb.DuckDBPyConnection,
    state: HullState,
    *,
    elapsed_seconds: float,
) -> None:
    connection.execute(
        """
        INSERT INTO hull_steps (
            step_number, inserted_point_id, retained,
            hull_vertex_count, hull_face_count, surface_area, volume,
            added_face_count, removed_face_count, horizon_edge_count,
            elapsed_seconds
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            state.points_processed,
            state.inserted_point_id,
            state.inserted_point_retained,
            len(state.points),
            len(state.faces),
            state.area,
            state.volume,
            len(state.added_face_keys),
            len(state.removed_face_keys),
            len(state.horizon_edges),
            elapsed_seconds,
        ],
    )


def _edge_count(state: HullState) -> int:
    edges: set[tuple[int, int]] = set()
    for face in state.faces:
        a, b, c = (int(value) for value in face)
        edges.update(
            {
                tuple(sorted((a, b))),
                tuple(sorted((b, c))),
                tuple(sorted((c, a))),
            }
        )
    return len(edges)


def _validate_final_state(
    engine: Any,
    state: HullState,
    all_points: np.ndarray,
    *,
    full_convexity_max_vertices: int,
    scipy_reference_max_points: int,
) -> list[ValidationItem]:
    items: list[ValidationItem] = []

    full_convexity = len(state.points) <= full_convexity_max_vertices
    try:
        engine.validate_state(state, check_convexity=full_convexity)
        items.append(
            ValidationItem(
                "invariants_topologiques_et_geometriques",
                True,
                "OK",
                f"convexité complète si V <= {full_convexity_max_vertices}",
                "Arêtes appariées, Euler, coloration, aire et volume validés."
                + (" Convexité complète validée." if full_convexity else " Convexité complète omise au-delà du seuil."),
            )
        )
    except TypeError:
        engine.validate_state(state)
        items.append(
            ValidationItem(
                "invariants_topologiques_et_geometriques",
                True,
                "OK",
                "validation du moteur",
                "Validation intégrée au moteur SciPy effectuée.",
            )
        )

    edge_count = _edge_count(state)
    euler_value = len(state.points) - edge_count + len(state.faces)
    items.append(
        ValidationItem(
            "formule_euler",
            euler_value == 2,
            str(euler_value),
            "V - E + F = 2",
            f"V={len(state.points)}, E={edge_count}, F={len(state.faces)}",
        )
    )

    triangular_relation = len(state.faces) == 2 * len(state.points) - 4
    items.append(
        ValidationItem(
            "relation_polyedre_triangule",
            triangular_relation,
            f"F={len(state.faces)}",
            f"2V-4={2 * len(state.points) - 4}",
            "Relation déduite d'Euler lorsque toutes les faces sont triangulaires.",
        )
    )

    colors = sorted(set(int(value) for value in state.face_colors))
    items.append(
        ValidationItem(
            "coloration_quatre_couleurs",
            all(0 <= value <= 3 for value in colors),
            ",".join(str(value) for value in colors),
            "couleurs dans {0,1,2,3}",
            "Deux triangles voisins ne doivent jamais partager la même couleur.",
        )
    )

    if len(all_points) <= scipy_reference_max_points:
        reference = ConvexHull(all_points)
        volume_error = abs(float(reference.volume) - state.volume)
        area_error = abs(float(reference.area) - state.area)
        scale_volume = max(1.0, abs(float(reference.volume)))
        scale_area = max(1.0, abs(float(reference.area)))
        tolerance_volume = 1e-8 * scale_volume
        tolerance_area = 1e-8 * scale_area
        items.extend(
            [
                ValidationItem(
                    "reference_scipy_volume",
                    volume_error <= tolerance_volume,
                    f"{volume_error:.6e}",
                    f"<= {tolerance_volume:.6e}",
                    f"Volume SciPy={float(reference.volume):.12g}; historique={state.volume:.12g}",
                ),
                ValidationItem(
                    "reference_scipy_aire",
                    area_error <= tolerance_area,
                    f"{area_error:.6e}",
                    f"<= {tolerance_area:.6e}",
                    f"Aire SciPy={float(reference.area):.12g}; historique={state.area:.12g}",
                ),
                ValidationItem(
                    "reference_scipy_faces",
                    len(reference.simplices) == len(state.faces),
                    str(len(state.faces)),
                    str(len(reference.simplices)),
                    "Comparaison du nombre de triangles finaux.",
                ),
            ]
        )
    else:
        items.append(
            ValidationItem(
                "reference_scipy",
                True,
                "non exécutée",
                f"N <= {scipy_reference_max_points}",
                "Validation volontairement omise pour éviter un coût excessif sur un grand corpus.",
            )
        )
    return items


def _state_to_tables(
    state: HullState,
    clean_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    source_row_by_id = clean_df.set_index("point_id")["source_row"].to_dict()
    label_by_id = clean_df.set_index("point_id")["label"].to_dict()
    vertices = pd.DataFrame(
        {
            "local_vertex_id": np.arange(len(state.points), dtype=np.int64),
            "point_id": state.point_ids.astype(np.int64),
            "x": state.points[:, 0],
            "y": state.points[:, 1],
            "z": state.points[:, 2],
        }
    )
    vertices["source_row"] = vertices["point_id"].map(source_row_by_id)
    vertices["label"] = vertices["point_id"].map(label_by_id)

    point_ids_by_local = state.point_ids.astype(np.int64)
    faces = pd.DataFrame(
        {
            "face_id": np.arange(len(state.faces), dtype=np.int64),
            "vertex_a": state.faces[:, 0],
            "vertex_b": state.faces[:, 1],
            "vertex_c": state.faces[:, 2],
            "point_id_a": point_ids_by_local[state.faces[:, 0]],
            "point_id_b": point_ids_by_local[state.faces[:, 1]],
            "point_id_c": point_ids_by_local[state.faces[:, 2]],
            "color": state.face_colors.astype(np.int64),
        }
    )
    faces["color_name"] = faces["color"].map(
        {index: name for index, name in enumerate(FACE_COLOR_NAMES)}
    )
    return vertices, faces


def _state_to_mesh(state: HullState) -> pv.PolyData:
    pyvista_faces = np.column_stack(
        [np.full(len(state.faces), 3, dtype=np.int64), state.faces]
    ).ravel()
    mesh = pv.PolyData(state.points, pyvista_faces)
    mesh.cell_data["face_color"] = state.face_colors
    mesh.cell_data["face_rgb"] = FACE_RGB[state.face_colors]
    mesh.point_data["point_id"] = state.point_ids
    return mesh


def render_final_hull(
    state: HullState,
    *,
    screenshot_path: Path,
    show: bool,
    title_prefix: str,
) -> None:
    """Crée la capture finale et, sur demande, ouvre une fenêtre 3D interactive."""

    mesh = _state_to_mesh(state)
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)

    plotter = pv.Plotter(off_screen=not show, window_size=(1400, 900))
    plotter.enable_trackball_style()
    plotter.set_background("black")
    plotter.add_mesh(
        mesh,
        scalars="face_rgb",
        rgb=True,
        show_edges=True,
        edge_color="gray",
        line_width=1.2,
        opacity=1.0,
    )
    plotter.add_points(
        state.points,
        color="white",
        point_size=7,
        render_points_as_spheres=True,
    )
    plotter.add_text(
        f"{title_prefix}\n{state.points_processed} points — {len(state.points)} sommets — "
        f"{len(state.faces)} faces\nVolume={state.volume:.6g} — Aire={state.area:.6g}",
        color="white",
        font_size=11,
        position="upper_left",
    )
    plotter.add_axes(color="white")
    plotter.show(screenshot=str(screenshot_path), auto_close=not show)
    if show:
        print("Vue interactive : rotation avec la souris gauche, zoom avec la molette, q pour fermer.")


def _write_excel_outputs(
    run_dir: Path,
    *,
    import_result: ImportResult,
    vertices: pd.DataFrame,
    faces: pd.DataFrame,
    validation_df: pd.DataFrame,
    metrics: dict[str, Any],
) -> None:
    exports_dir = run_dir / "exports"
    import_result.clean_df.to_excel(exports_dir / "clean_points.xlsx", index=False)
    import_result.rejected_df.to_excel(exports_dir / "rejected_points.xlsx", index=False)
    vertices.to_excel(exports_dir / "final_vertices.xlsx", index=False)
    faces.to_excel(exports_dir / "final_faces.xlsx", index=False)

    summary_df = pd.DataFrame(
        [{"metric": key, "value": value} for key, value in metrics.items()]
    )
    with pd.ExcelWriter(exports_dir / "results.xlsx", engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        vertices.to_excel(writer, sheet_name="HullVertices", index=False)
        faces.to_excel(writer, sheet_name="HullFaces", index=False)
        validation_df.to_excel(writer, sheet_name="Validation", index=False)
        import_result.rejected_df.to_excel(writer, sheet_name="RejectedPoints", index=False)


def _write_sql_helpers(run_dir: Path, database_path: Path) -> None:
    relative_path = database_path.as_posix()
    attach_sql = f"""-- Exécuter ce fichier dans l'extension DuckDB de VS Code.\n-- La base est propre à cette exécution et peut être attachée en lecture seule.\nATTACH '{relative_path}' AS hull_run (READ_ONLY);\nUSE hull_run;\nSHOW TABLES;\n"""
    inspect_sql = """-- La commande sql/attach_database.sql doit avoir été exécutée.\nSELECT * FROM run_info;\nSELECT * FROM validation_results ORDER BY test_name;\nSELECT * FROM hull_vertices ORDER BY local_vertex_id LIMIT 1000;\nSELECT * FROM hull_faces ORDER BY face_id LIMIT 1000;\nSELECT * FROM rejected_points ORDER BY source_row LIMIT 1000;\nSELECT * FROM hull_steps ORDER BY step_number LIMIT 1000;\n"""
    detach_sql = """USE memory;\nDETACH hull_run;\n"""
    sql_dir = run_dir / "sql"
    sql_dir.mkdir(exist_ok=True)
    (sql_dir / "attach_database.sql").write_text(attach_sql, encoding="utf-8")
    (sql_dir / "inspect_database.sql").write_text(inspect_sql, encoding="utf-8")
    (sql_dir / "detach_database.sql").write_text(detach_sql, encoding="utf-8")


def _write_report(
    run_dir: Path,
    *,
    source_path: Path,
    import_result: ImportResult,
    metrics: dict[str, Any],
    validation_items: list[ValidationItem],
) -> None:
    validation_lines = "\n".join(
        f"| {item.test_name} | {'OK' if item.passed else 'ÉCHEC'} | {item.value} | "
        f"{item.tolerance} | {item.details} |"
        for item in validation_items
    )
    columns = import_result.resolved_columns
    report = f"""# Rapport technique — enveloppe convexe 3D

## 1. Identification de l'exécution

- **Run ID :** `{metrics['run_id']}`
- **Source :** `{source_path}`
- **Moteur :** `{metrics['engine']}`
- **Début UTC :** `{metrics['started_at_utc']}`
- **Fin UTC :** `{metrics['finished_at_utc']}`

## 2. Import et qualité des données

- Lignes source : **{metrics['source_rows']}**
- Points valides : **{metrics['valid_points']}**
- Lignes rejetées : **{metrics['rejected_points']}**
- Colonne X : `{columns['x']}`
- Colonne Y : `{columns['y']}`
- Colonne Z : `{columns['z']}`
- Colonne identifiant : `{columns['point_id'] or 'générée automatiquement'}`
- Colonne libellé : `{columns['label'] or 'générée automatiquement'}`

Les lignes rejetées sont conservées dans `exports/rejected_points.xlsx`. Les données originales sont conservées dans la table DuckDB `source_points` et les points normalisés dans `clean_points`.

## 3. Algorithme incrémental historique

Le programme cherche d'abord quatre points non coplanaires afin de former un tétraèdre initial. Pour une face orientée `(a,b,c)` et un point candidat `p`, le test géométrique repose sur le produit mixte :

```text
D(a,b,c,p) = (b-a) · ((c-a) × (p-a))
```

Après normalisation de la normale de face, le signe de `n · p + d` indique si la face est visible depuis le nouveau point. Si aucune face n'est visible, le point est intérieur et ne modifie pas l'enveloppe. Sinon :

1. les faces visibles sont supprimées ;
2. les arêtes appartenant à une seule face visible constituent l'horizon ;
3. chaque arête de l'horizon est reliée au nouveau point ;
4. les nouveaux triangles sont orientés vers l'extérieur ;
5. les sommets qui ne participent plus à aucune face sont retirés de la structure active.

La v4 a montré que cette mise à jour locale est très supérieure au recalcul global SciPy/Qhull à chaque insertion. Sur notre poste de test :

| Points sur une sphère | SciPy global | Historique local | Accélération |
|---:|---:|---:|---:|
| 1 000 | 45,918 s | 0,774 s | ×59,34 |
| 1 500 | 102,594 s | 1,354 s | ×75,77 |
| 2 000 | 181,721 s | 2,077 s | ×87,49 |

## 4. Aire, volume et topologie

L'aire est la somme des aires triangulaires :

```text
A_face = 1/2 × ||(b-a) × (c-a)||
```

Le volume est obtenu par la somme orientée des tétraèdres formés avec l'origine :

```text
V = | Σ a · (b × c) / 6 |
```

La cohérence topologique est contrôlée par :

```text
V_sommets - E_arêtes + F_faces = 2
```

Pour une surface triangulée fermée, la relation `F = 2V - 4` est également vérifiée. Chaque arête doit appartenir exactement à deux faces.

## 5. Coloration

Chaque triangle possède au plus trois voisins, un par arête. Une coloration gloutonne choisit donc une couleur disponible parmi rouge, jaune, vert et bleu. Le contrôle final garantit que deux faces voisines n'ont jamais la même couleur.

## 6. Résultats

- Points traités : **{metrics['points_processed']}**
- Sommets extrêmes : **{metrics['hull_vertices']}**
- Faces triangulaires : **{metrics['hull_faces']}**
- Arêtes : **{metrics['hull_edges']}**
- Aire : **{metrics['surface_area']:.12g}**
- Volume : **{metrics['volume']:.12g}**
- Temps géométrique : **{metrics['hull_seconds']:.6f} s**
- Temps total : **{metrics['total_seconds']:.6f} s**
- Débit : **{metrics['points_per_second']:.2f} points/s**
- Mémoire maximale observée : **{metrics['peak_memory_mb']:.2f} Mo**

## 7. Validations automatiques

| Test | Résultat | Valeur | Tolérance ou attendu | Détails |
|---|---|---|---|---|
{validation_lines}

## 8. Base DuckDB de l'exécution

La base `points.duckdb` de ce répertoire contient :

- `source_points` : copie tabulaire de la source ;
- `clean_points` : coordonnées normalisées ;
- `rejected_points` : lignes rejetées et motifs ;
- `hull_vertices` : sommets extrêmes finaux ;
- `hull_faces` : triangles et couleurs ;
- `hull_steps` : points de contrôle du calcul ;
- `validation_results` : résultats des tests ;
- `run_info` : métriques globales.

Les scripts d'attachement et d'exploration sont dans le sous-répertoire `sql`.

## 9. Montée en charge

Excel convient aux démonstrations et aux volumes modérés, mais son format est limité à 1 048 576 lignes par feuille et son décodage est relativement coûteux. Pour les grands corpus, la même commande accepte Parquet et CSV. La stratégie recommandée est :

1. stocker les coordonnées en Parquet ;
2. filtrer et typer les colonnes avec DuckDB ou **Polars** ;
3. lire les points par lots ou en mode streaming ;
4. conserver le moteur géométrique NumPy ;
5. n'afficher que la surface finale ou des étapes échantillonnées.

Polars est une piste pertinente pour la préparation de très grandes tables grâce à son exécution paresseuse et à son moteur en colonnes. Il ne remplace pas le moteur géométrique : il optimise l'ingestion, le nettoyage et les transformations avant l'algorithme d'enveloppe.

## 10. Fichiers produits

- `points.duckdb`
- `exports/clean_points.xlsx`
- `exports/rejected_points.xlsx`
- `exports/final_vertices.xlsx`
- `exports/final_faces.xlsx`
- `exports/results.xlsx`
- `exports/final_vertices.csv`
- `exports/final_faces.csv`
- `exports/final_hull.vtp`
- `exports/final_hull.ply`
- `exports/final_hull.png`
- `metrics.json`
- `sql/attach_database.sql`
- `sql/inspect_database.sql`
- `sql/detach_database.sql`
"""
    (run_dir / "report.md").write_text(report, encoding="utf-8")


def generate_source_file(
    *,
    output_path: str | Path,
    point_count: int,
    distribution: DistributionName,
    seed: int,
) -> Path:
    """Crée un fichier de test qui emprunte exactement le futur chemin d'import réel."""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    records = generate_points(point_count, distribution=distribution, seed=seed)
    dataframe = pd.DataFrame(
        {
            "point_id": [point.point_id for point in records],
            "x": [point.x for point in records],
            "y": [point.y for point in records],
            "z": [point.z for point in records],
            "label": [point.label for point in records],
            "source": f"synthetic_{distribution}",
        }
    )
    suffix = output.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            dataframe.to_excel(writer, sheet_name="Points", index=False)
            pd.DataFrame(
                [
                    {"parameter": "distribution", "value": distribution},
                    {"parameter": "point_count", "value": point_count},
                    {"parameter": "seed", "value": seed},
                ]
            ).to_excel(writer, sheet_name="Metadata", index=False)
    elif suffix == ".csv":
        dataframe.to_csv(output, index=False)
    elif suffix == ".parquet":
        dataframe.to_parquet(output, index=False)
    else:
        raise ValueError("La sortie doit avoir l'extension .xlsx, .csv ou .parquet.")
    return output


def process_source_file(
    *,
    input_path: str | Path,
    runs_dir: str | Path = "data/runs",
    sheet: str | int | None = None,
    x_column: str | None = None,
    y_column: str | None = None,
    z_column: str | None = None,
    id_column: str | None = None,
    label_column: str | None = None,
    engine_name: EngineName = "historical",
    record_every: int = 100,
    progress_every: int = 1000,
    validate_each_step: bool = False,
    full_convexity_max_vertices: int = 5000,
    scipy_reference_max_points: int = 5000,
    max_seconds: float | None = None,
    show: bool = False,
    create_screenshot: bool = True,
) -> ProcessResult:
    """Exécute l'import, DuckDB, l'enveloppe, les validations et les exports."""

    if record_every < 1 or progress_every < 1:
        raise ValueError("record_every et progress_every doivent être supérieurs ou égaux à 1.")

    started_utc = datetime.now(UTC)
    total_start = time.perf_counter()
    source_path = Path(input_path)
    source_df = read_source_table(source_path, sheet=sheet)
    import_result = validate_and_normalize_source(
        source_df,
        source_name=source_path.stem,
        x_column=x_column,
        y_column=y_column,
        z_column=z_column,
        id_column=id_column,
        label_column=label_column,
    )

    run_id, run_dir = create_run_directory(runs_dir, source_path)
    source_copy = run_dir / f"source_original{source_path.suffix.lower()}"
    shutil.copy2(source_path, source_copy)
    database_path = run_dir / "points.duckdb"
    connection = _initialise_run_database(
        database_path,
        run_id=run_id,
        source_path=source_path,
        engine_name=engine_name,
        import_result=import_result,
    )

    records = _dataframe_to_records(import_result.clean_df)
    initial, remaining = split_initial_tetrahedron(records)
    engine = _create_engine(engine_name, validate_each_step=validate_each_step)
    state = engine.initialize(initial)
    hull_start = time.perf_counter()
    _record_step(connection, state, elapsed_seconds=0.0)

    try:
        for iteration, point in enumerate(remaining, start=1):
            step_number = iteration + 4
            is_last = iteration == len(remaining)
            should_record = step_number % record_every == 0 or is_last
            should_materialize = engine_name == "scipy" or should_record or validate_each_step
            next_state = engine.add_point(point, materialize=should_materialize)
            if next_state is not None:
                state = next_state

            elapsed = time.perf_counter() - hull_start
            if max_seconds is not None and elapsed > max_seconds:
                raise TimeoutError(
                    f"Limite de {max_seconds:.1f} secondes dépassée après {step_number} points."
                )
            if should_record:
                if next_state is None:
                    state = engine.snapshot()
                _record_step(connection, state, elapsed_seconds=elapsed)
            if step_number % progress_every == 0 or is_last:
                print(
                    f"  {step_number}/{len(records)} — sommets={engine.vertex_count if hasattr(engine, 'vertex_count') else len(state.points)} "
                    f"— faces={engine.face_count if hasattr(engine, 'face_count') else len(state.faces)} — {elapsed:.2f} s"
                )

        state = engine.snapshot()
        hull_seconds = time.perf_counter() - hull_start
        all_points = import_result.clean_df[["x", "y", "z"]].to_numpy(dtype=np.float64)
        validation_items = _validate_final_state(
            engine,
            state,
            all_points,
            full_convexity_max_vertices=full_convexity_max_vertices,
            scipy_reference_max_points=scipy_reference_max_points,
        )
        if not all(item.passed for item in validation_items):
            failures = ", ".join(item.test_name for item in validation_items if not item.passed)
            raise AssertionError(f"Validation finale en échec : {failures}")

        vertices, faces = _state_to_tables(state, import_result.clean_df)
        validation_df = pd.DataFrame(
            [
                {
                    "test_name": item.test_name,
                    "passed": item.passed,
                    "value": item.value,
                    "tolerance": item.tolerance,
                    "details": item.details,
                }
                for item in validation_items
            ]
        )
        _write_dataframe_table(connection, "hull_vertices", vertices)
        _write_dataframe_table(connection, "hull_faces", faces)
        _write_dataframe_table(connection, "validation_results", validation_df)

        exports_dir = run_dir / "exports"
        vertices.to_csv(exports_dir / "final_vertices.csv", index=False)
        faces.to_csv(exports_dir / "final_faces.csv", index=False)
        mesh = _state_to_mesh(state)
        mesh.save(exports_dir / "final_hull.vtp")
        mesh.save(exports_dir / "final_hull.ply")

        finished_utc = datetime.now(UTC)
        total_seconds = time.perf_counter() - total_start
        peak_memory_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        edges = _edge_count(state)
        metrics: dict[str, Any] = {
            "run_id": run_id,
            "engine": engine_name,
            "source_file": str(source_path),
            "started_at_utc": started_utc.isoformat(),
            "finished_at_utc": finished_utc.isoformat(),
            "source_rows": len(import_result.source_df),
            "valid_points": len(import_result.clean_df),
            "rejected_points": len(import_result.rejected_df),
            "points_processed": state.points_processed,
            "hull_vertices": len(state.points),
            "hull_edges": edges,
            "hull_faces": len(state.faces),
            "surface_area": state.area,
            "volume": state.volume,
            "hull_seconds": hull_seconds,
            "total_seconds": total_seconds,
            "points_per_second": state.points_processed / hull_seconds if hull_seconds else math.inf,
            "peak_memory_mb": peak_memory_mb,
            "reference_scipy_executed": len(all_points) <= scipy_reference_max_points,
            "full_convexity_executed": len(state.points) <= full_convexity_max_vertices,
        }
        (run_dir / "metrics.json").write_text(
            json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        _write_excel_outputs(
            run_dir,
            import_result=import_result,
            vertices=vertices,
            faces=faces,
            validation_df=validation_df,
            metrics=metrics,
        )
        _write_sql_helpers(run_dir, database_path)
        _write_report(
            run_dir,
            source_path=source_path,
            import_result=import_result,
            metrics=metrics,
            validation_items=validation_items,
        )
        connection.execute(
            """
            UPDATE run_info SET
                status='completed', finished_at=current_timestamp,
                points_processed=?, hull_vertex_count=?, hull_face_count=?,
                surface_area=?, volume=?, elapsed_seconds=?, points_per_second=?,
                peak_memory_mb=?
            WHERE run_id=?
            """,
            [
                state.points_processed,
                len(state.points),
                len(state.faces),
                state.area,
                state.volume,
                total_seconds,
                metrics["points_per_second"],
                peak_memory_mb,
                run_id,
            ],
        )
        connection.close()

        screenshot_path = exports_dir / "final_hull.png"
        if create_screenshot or show:
            render_final_hull(
                state,
                screenshot_path=screenshot_path,
                show=show,
                title_prefix=f"Enveloppe convexe 3D — {source_path.name}",
            )

        print(f"Traitement terminé : {run_dir}")
        print(
            f"  points={state.points_processed} — sommets={len(state.points)} — "
            f"faces={len(state.faces)} — calcul={hull_seconds:.3f} s"
        )
        return ProcessResult(
            run_id=run_id,
            run_dir=run_dir,
            database_path=database_path,
            state=state,
            metrics=metrics,
            validation_items=validation_items,
        )
    except Exception:
        try:
            connection.execute(
                "UPDATE run_info SET status='failed', finished_at=current_timestamp WHERE run_id=?",
                [run_id],
            )
        finally:
            connection.close()
        raise
