from __future__ import annotations

import argparse
import contextlib
import csv
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pyvista as pv
from scipy.spatial import ConvexHull

from hull_engine import HullState, ScipyRebuildHull3D, split_initial_tetrahedron
from incremental_hull_engine import HistoricalIncrementalHull3D
from pipeline_v5 import generate_source_file, process_source_file
from storage import (
    DistributionName,
    PointRecord,
    connect_database,
    finish_run,
    generate_points,
    load_points,
    record_benchmark,
    record_step,
    replace_points,
    seed_demo_points,
    start_run,
)

EngineName = Literal["scipy", "historical"]

FACE_RGB = np.array(
    [
        [211, 47, 47],
        [251, 192, 45],
        [56, 142, 60],
        [25, 118, 210],
    ],
    dtype=np.uint8,
)

ENGINE_DATABASE_NAMES: dict[EngineName, str] = {
    "scipy": "scipy_rebuild",
    "historical": "historical_incremental",
}

ENGINE_LABELS: dict[EngineName, str] = {
    "scipy": "SciPy/Qhull — reconstruction globale",
    "historical": "Historique — faces visibles et horizon",
}


@dataclass(slots=True)
class TimedHullResult:
    """Résume une exécution complète ou interrompue par sa limite de temps."""

    state: HullState
    processed_points: int
    completed: bool
    hull_seconds: float


@dataclass(slots=True)
class ReferenceCheck:
    """Résume la comparaison finale avec une enveloppe SciPy calculée une seule fois."""

    valid: bool
    vertex_ids_equal: bool
    face_count_equal: bool
    volume_error: float
    area_error: float


class HullVisualizer:
    """Affiche la construction incrémentale puis une scène finale interactive."""

    def __init__(self, *, delay: float, hold: bool, engine_label: str) -> None:
        self.delay = delay
        self.hold = hold
        self.engine_label = engine_label
        self.plotter = pv.Plotter(window_size=(1280, 800))
        self.plotter.enable_trackball_style()
        self.plotter.set_background("white")
        self.started = False
        self.camera_position = None

    def render(
        self,
        state: HullState,
        processed_points: np.ndarray,
        inserted_point: np.ndarray | None,
        *,
        final: bool = False,
    ) -> None:
        """Reconstruit la scène tout en conservant l'angle choisi par la caméra."""

        if self.started:
            self.camera_position = self.plotter.camera_position
        self.plotter.clear_actors()

        background_color = "black" if final else "white"
        text_color = "white" if final else "black"
        edge_color = "gray" if final else "black"
        self.plotter.set_background(background_color)

        mesh = _state_to_mesh(state)
        self.plotter.add_mesh(
            mesh,
            scalars="face_rgb",
            rgb=True,
            show_edges=True,
            edge_color=edge_color,
            line_width=1.5,
            opacity=1.0,
            name="surface",
        )

        # Les points déjà injectés aident à comprendre l'animation, mais ils sont
        # retirés de la vue finale afin de préserver la lisibilité du polyèdre.
        if not final and len(processed_points):
            self.plotter.add_points(
                processed_points,
                color="lightgray",
                point_size=8,
                render_points_as_spheres=True,
                name="points_traites",
            )

        self.plotter.add_points(
            state.points,
            color="white",
            point_size=13,
            render_points_as_spheres=True,
            name="sommets_extremes",
        )

        if inserted_point is not None and not final:
            self.plotter.add_points(
                inserted_point.reshape(1, 3),
                color="black",
                point_size=18,
                render_points_as_spheres=True,
                name="point_insere",
            )

        if not final:
            horizon = _horizon_to_polydata(state)
            if horizon is not None:
                self.plotter.add_mesh(
                    horizon,
                    color="black",
                    line_width=7,
                    name="horizon",
                )

        if final:
            title = (
                "Surface convexe finale\n"
                f"Moteur : {self.engine_label}\n"
                f"{state.points_processed} points traités — "
                f"{len(state.points)} sommets — {len(state.faces)} faces\n"
                f"Volume : {state.volume:.4f} — Aire : {state.area:.4f}\n"
                "Souris gauche : rotation — molette : zoom — "
                "souris centrale : déplacement — touche q : fermer"
            )
        else:
            decision = (
                "conservé sur l'enveloppe"
                if state.inserted_point_retained
                else "rejeté comme point intérieur"
            )
            title = (
                f"{self.engine_label}\n"
                f"Étape {state.points_processed} — "
                f"{len(state.points)} sommets — {len(state.faces)} faces\n"
                f"Volume : {state.volume:.4f} — Aire : {state.area:.4f}"
            )
            if state.inserted_point_id is not None:
                title += f"\nPoint {state.inserted_point_id} : {decision}"

        self.plotter.add_text(
            title,
            position="upper_left",
            font_size=11,
            color=text_color,
        )
        self.plotter.add_axes(color=text_color)

        if not self.started:
            self.plotter.show(auto_close=False, interactive_update=True)
            self.started = True
        else:
            if self.camera_position is not None:
                self.plotter.camera_position = self.camera_position
            self.plotter.reset_camera_clipping_range()
            self.plotter.update()

        if self.delay > 0 and not final:
            time.sleep(self.delay)

    def save_screenshot(self, path: Path) -> None:
        """Enregistre l'état final avec le même fond noir que la fenêtre."""

        path.parent.mkdir(parents=True, exist_ok=True)
        self.plotter.screenshot(path)

    def finish(self) -> None:
        """Démarre la boucle interactive finale ou ferme immédiatement la fenêtre."""

        if not self.started:
            return
        if self.hold:
            print(
                "Surface finale interactive : utiliser la souris pour tourner, "
                "puis appuyer sur q pour fermer."
            )
            self.plotter.show(
                interactive=True,
                auto_close=True,
                interactive_update=False,
            )
        else:
            self.plotter.close()


def build_parser() -> argparse.ArgumentParser:
    """Décrit les commandes de la base, du rendu et des benchmarks comparatifs."""

    parser = argparse.ArgumentParser(
        description="Enveloppe convexe 3D : référence SciPy et moteur historique local."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-db", help="Initialiser les vingt points.")
    init_parser.add_argument("--database", default="data/points.duckdb")
    init_parser.add_argument("--reset", action="store_true")

    generate_parser = subparsers.add_parser(
        "generate", help="Remplacer la table points par un nuage synthétique."
    )
    generate_parser.add_argument("--database", default="data/points.duckdb")
    generate_parser.add_argument("--points", type=int, required=True)
    generate_parser.add_argument(
        "--distribution",
        choices=("volume", "sphere", "cube"),
        default="volume",
    )
    generate_parser.add_argument("--seed", type=int, default=42)

    run_parser = subparsers.add_parser("run", help="Construire l'enveloppe point par point.")
    _add_run_arguments(run_parser)

    demo_parser = subparsers.add_parser(
        "demo", help="Réinitialiser la base puis animer les vingt points."
    )
    _add_run_arguments(demo_parser)

    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="Comparer les moteurs sur les mêmes tailles et distributions.",
    )
    benchmark_parser.add_argument("--database", default="data/points.duckdb")
    benchmark_parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=[100, 500, 1000],
    )
    benchmark_parser.add_argument(
        "--distributions",
        choices=("volume", "sphere", "cube"),
        nargs="+",
        default=["volume", "sphere"],
    )
    benchmark_parser.add_argument(
        "--engines",
        choices=("scipy", "historical"),
        nargs="+",
        default=["scipy", "historical"],
        help="SciPy reconstruit tout ; historical met à jour uniquement la zone visible.",
    )
    benchmark_parser.add_argument("--seed", type=int, default=42)
    benchmark_parser.add_argument(
        "--max-seconds",
        type=float,
        default=300.0,
        help="Limite appliquée séparément à chaque moteur et chaque taille.",
    )
    benchmark_parser.add_argument(
        "--progress-every",
        type=int,
        default=250,
        help="Fréquence d'affichage de la progression dans le terminal.",
    )
    benchmark_parser.add_argument(
        "--validate-each-step",
        action="store_true",
        help="Active les contrôles complets à chaque point, plus sûrs mais plus coûteux.",
    )
    benchmark_parser.add_argument(
        "--skip-reference-check",
        action="store_true",
        help="Désactive la comparaison finale unique avec SciPy/Qhull.",
    )
    benchmark_parser.add_argument(
        "--reference-max-points",
        type=int,
        default=5000,
        help="Taille maximale validée automatiquement par un Qhull final.",
    )
    benchmark_parser.add_argument(
        "--output",
        default="data/benchmarks/benchmark_results_v4.csv",
    )

    generate_file_parser = subparsers.add_parser(
        "generate-file",
        help="Créer un fichier Excel, CSV ou Parquet de test sans contourner le pipeline réel.",
    )
    generate_file_parser.add_argument("--points", type=int, required=True)
    generate_file_parser.add_argument(
        "--distribution", choices=("volume", "sphere", "cube"), default="volume"
    )
    generate_file_parser.add_argument("--seed", type=int, default=42)
    generate_file_parser.add_argument(
        "--output", required=True, help="Chemin .xlsx, .csv ou .parquet."
    )

    process_parser = subparsers.add_parser(
        "process",
        help="Importer un vrai fichier tabulaire, créer une base DuckDB par run et calculer l'enveloppe.",
    )
    process_parser.add_argument("--input", required=True)
    process_parser.add_argument("--runs-dir", default="data/runs")
    process_parser.add_argument("--sheet", default=None)
    process_parser.add_argument("--x-column", default=None)
    process_parser.add_argument("--y-column", default=None)
    process_parser.add_argument("--z-column", default=None)
    process_parser.add_argument("--id-column", default=None)
    process_parser.add_argument("--label-column", default=None)
    process_parser.add_argument(
        "--engine", choices=("historical", "scipy"), default="historical"
    )
    process_parser.add_argument("--record-every", type=int, default=100)
    process_parser.add_argument("--progress-every", type=int, default=1000)
    process_parser.add_argument("--validate-each-step", action="store_true")
    process_parser.add_argument("--full-convexity-max-vertices", type=int, default=5000)
    process_parser.add_argument("--scipy-reference-max-points", type=int, default=5000)
    process_parser.add_argument("--max-seconds", type=float, default=None)
    process_parser.add_argument("--show", action="store_true")
    process_parser.add_argument(
        "--no-screenshot", action="store_true", help="Ne pas créer final_hull.png."
    )

    inspect_parser = subparsers.add_parser(
        "inspect", help="Afficher les dernières métriques stockées dans DuckDB."
    )
    inspect_parser.add_argument("--database", default="data/points.duckdb")
    return parser


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    """Ajoute les options partagées par les commandes run et demo."""

    parser.add_argument("--database", default="data/points.duckdb")
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument(
        "--engine",
        choices=("scipy", "historical"),
        default="historical",
    )
    parser.add_argument("--delay", type=float, default=0.55)
    parser.add_argument("--display-every", type=int, default=1)
    parser.add_argument("--record-every", type=int, default=1)
    parser.add_argument("--final-only", action="store_true")
    parser.add_argument(
        "--validate-each-step",
        action="store_true",
        help="Contrôle tous les invariants à chaque insertion ; utile surtout pour les petits jeux.",
    )
    parser.add_argument(
        "--skip-reference-check",
        action="store_true",
        help="Ignore la comparaison finale avec SciPy/Qhull.",
    )
    parser.add_argument(
        "--reference-max-points",
        type=int,
        default=5000,
        help="Au-delà de cette taille, la validation Qhull finale est ignorée automatiquement.",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--no-hold", action="store_true")


def _create_engine(
    engine_name: EngineName,
    *,
    validate_each_step: bool,
):
    """Construit le moteur demandé avec une interface commune."""

    if engine_name == "scipy":
        return ScipyRebuildHull3D(validate_each_step=validate_each_step)
    if engine_name == "historical":
        return HistoricalIncrementalHull3D(validate_each_step=validate_each_step)
    raise ValueError(f"Moteur inconnu : {engine_name}")


def command_init_database(database: str, *, reset: bool) -> None:
    """Crée la base locale et garantit la présence du jeu de démonstration."""

    connection = connect_database(database)
    try:
        count = seed_demo_points(connection, reset=reset)
    finally:
        connection.close()
    print(f"Base prête : {database} — {count} points disponibles.")


def command_generate(
    *,
    database: str,
    point_count: int,
    distribution: DistributionName,
    seed: int,
) -> None:
    """Génère un nuage reproductible puis le stocke dans la table points."""

    points = generate_points(point_count, distribution=distribution, seed=seed)
    connection = connect_database(database)
    try:
        inserted = replace_points(connection, points)
    finally:
        connection.close()
    print(
        f"Nuage créé : {inserted} points — distribution={distribution} — "
        f"seed={seed} — base={database}"
    )


def command_run(
    *,
    database: str,
    output_dir: str,
    engine_name: EngineName,
    delay: float,
    display_every: int,
    record_every: int,
    final_only: bool,
    validate_each_step: bool,
    reference_check: bool,
    reference_max_points: int,
    headless: bool,
    hold: bool,
) -> HullState:
    """Exécute la chaîne DuckDB, moteur géométrique, audit et exports."""

    if display_every < 1:
        raise ValueError("--display-every doit être supérieur ou égal à 1.")
    if record_every < 1:
        raise ValueError("--record-every doit être supérieur ou égal à 1.")

    connection = connect_database(database)
    visualizer: HullVisualizer | None = None
    run_id = uuid.uuid4().hex[:12]
    try:
        points = load_points(connection)
        if not points:
            raise RuntimeError("La base ne contient aucun point. Lancer init-db ou generate.")

        initial_points, remaining_points = split_initial_tetrahedron(points)
        engine = _create_engine(
            engine_name,
            validate_each_step=validate_each_step,
        )
        state = engine.initialize(initial_points)
        start_run(
            connection,
            run_id,
            len(points),
            ENGINE_DATABASE_NAMES[engine_name],
        )
        _record_state(connection, run_id, state)

        processed_records = list(initial_points) if not final_only and not headless else []
        if not headless:
            visualizer = HullVisualizer(
                delay=delay,
                hold=hold,
                engine_label=ENGINE_LABELS[engine_name],
            )
            if not final_only:
                visualizer.render(
                    state,
                    processed_points=_records_to_coordinates(processed_records),
                    inserted_point=None,
                )

        for iteration, point in enumerate(remaining_points, start=1):
            next_step = 4 + iteration
            is_last = iteration == len(remaining_points)
            should_record = next_step % record_every == 0 or is_last
            should_render = (
                visualizer is not None
                and not final_only
                and (next_step % display_every == 0 or is_last)
            )
            should_materialize = (
                engine_name == "scipy"
                or should_record
                or should_render
                or validate_each_step
            )

            next_state = engine.add_point(
                point,
                materialize=should_materialize,
            )
            if next_state is not None:
                state = next_state

            if processed_records is not None and not final_only and not headless:
                processed_records.append(point)

            if should_record:
                if next_state is None:
                    state = engine.snapshot()
                _record_state(connection, run_id, state)

            should_print = next_step % display_every == 0 or is_last or len(points) <= 100
            if should_print:
                if engine_name == "historical":
                    vertex_count = engine.vertex_count
                    face_count = engine.face_count
                    current_volume = engine.volume
                    retained = engine.inserted_point_retained
                else:
                    vertex_count = len(state.points)
                    face_count = len(state.faces)
                    current_volume = state.volume
                    retained = state.inserted_point_retained
                print(
                    f"Étape {next_step:07d} — point {point.point_id:07d} — "
                    f"{'retenu' if retained else 'intérieur'} — "
                    f"sommets={vertex_count} — faces={face_count} — "
                    f"V={current_volume:.4f}"
                )

            if should_render:
                if next_state is None:
                    state = engine.snapshot()
                visualizer.render(
                    state,
                    processed_points=_records_to_coordinates(processed_records),
                    inserted_point=point.coordinates,
                )

        if engine_name == "historical":
            engine.validate_state(
                state,
                check_convexity=len(points) <= reference_max_points,
            )
        else:
            engine.validate_state(state)

        finish_run(
            connection,
            run_id=run_id,
            points_processed=len(points),
            hull_vertex_count=len(state.points),
            hull_face_count=len(state.faces),
            surface_area=state.area,
            volume=state.volume,
        )

        output_path = Path(output_dir)
        export_results(
            state,
            output_path,
            run_id=run_id,
            engine_name=engine_name,
        )
        if visualizer is not None:
            visualizer.render(
                state,
                processed_points=np.empty((0, 3), dtype=np.float64),
                inserted_point=None,
                final=True,
            )
            visualizer.save_screenshot(output_path / "final_hull.png")

        check: ReferenceCheck | None = None
        if reference_check and len(points) <= reference_max_points:
            check = _check_against_scipy(points, state)
            if not check.valid:
                raise AssertionError(
                    "Le résultat final diffère de la référence SciPy/Qhull : "
                    f"erreur volume={check.volume_error:.3e}, "
                    f"erreur aire={check.area_error:.3e}."
                )

        print(
            f"Exécution {run_id} terminée avec {ENGINE_LABELS[engine_name]} : "
            f"{len(points)} points, {len(state.points)} sommets, "
            f"{len(state.faces)} faces, volume {state.volume:.6f}."
        )
        if check is not None:
            print(
                "Validation finale SciPy/Qhull réussie — "
                f"écart volume={check.volume_error:.3e}, aire={check.area_error:.3e}."
            )
        elif reference_check:
            print(
                "Validation SciPy/Qhull ignorée automatiquement : "
                f"{len(points)} points dépassent la limite {reference_max_points}."
            )
        return state
    except Exception:
        # L'échec de journalisation ne doit pas masquer l'exception initiale.
        with contextlib.suppress(Exception):
            connection.execute(
                """
                UPDATE hull_runs
                SET status = 'failed', finished_at = CURRENT_TIMESTAMP
                WHERE run_id = ?
                """,
                [run_id],
            )
        raise
    finally:
        # La connexion est libérée avant la scène finale afin que l'extension
        # DuckDB de VS Code puisse rattacher ensuite le fichier en lecture seule.
        connection.close()
        if visualizer is not None:
            visualizer.finish()


def _record_state(connection, run_id: str, state: HullState) -> None:
    """Centralise l'écriture des métriques d'une étape dans DuckDB."""

    record_step(
        connection,
        run_id=run_id,
        step_number=state.points_processed,
        inserted_point_id=state.inserted_point_id,
        point_retained=state.inserted_point_retained,
        hull_vertex_count=len(state.points),
        hull_face_count=len(state.faces),
        surface_area=state.area,
        volume=state.volume,
        added_face_count=len(state.added_face_keys),
        removed_face_count=len(state.removed_face_keys),
    )


def command_benchmark(
    *,
    database: str,
    sizes: list[int],
    distributions: list[DistributionName],
    engines: list[EngineName],
    seed: int,
    max_seconds: float,
    progress_every: int,
    validate_each_step: bool,
    reference_check: bool,
    reference_max_points: int,
    output: str,
) -> None:
    """Compare les moteurs sur exactement les mêmes points et dans le même ordre."""

    if any(size < 4 for size in sizes):
        raise ValueError("Toutes les tailles doivent être supérieures ou égales à 4.")
    if max_seconds <= 0:
        raise ValueError("--max-seconds doit être strictement positif.")

    ordered_sizes = sorted(set(sizes))
    ordered_engines = list(dict.fromkeys(engines))
    rows: list[dict[str, object]] = []
    skip_larger_sizes: dict[tuple[str, str], bool] = {}

    connection = connect_database(database)
    try:
        for distribution in distributions:
            for requested_points in ordered_sizes:
                total_start = time.perf_counter()

                generation_start = time.perf_counter()
                points = generate_points(
                    requested_points,
                    distribution=distribution,
                    seed=seed,
                )
                generation_seconds = time.perf_counter() - generation_start

                write_start = time.perf_counter()
                replace_points(connection, points)
                database_write_seconds = time.perf_counter() - write_start

                read_start = time.perf_counter()
                loaded_points = load_points(connection)
                database_read_seconds = time.perf_counter() - read_start
                shared_setup_seconds = time.perf_counter() - total_start

                scipy_seconds_for_speedup: float | None = None
                pending_database_rows: list[tuple[dict[str, object], TimedHullResult, ReferenceCheck | None]] = []

                for engine_name in ordered_engines:
                    skip_key = (distribution, engine_name)
                    if skip_larger_sizes.get(skip_key, False):
                        continue

                    print(
                        f"\nBenchmark {distribution} — {requested_points} points — "
                        f"moteur={engine_name} — limite {max_seconds:.0f} s"
                    )
                    timed_result = _compute_timed_hull(
                        loaded_points,
                        engine_name=engine_name,
                        max_seconds=max_seconds,
                        progress_every=progress_every,
                        validate_each_step=validate_each_step,
                    )
                    state = timed_result.state

                    check: ReferenceCheck | None = None
                    if (
                        reference_check
                        and timed_result.completed
                        and requested_points <= reference_max_points
                    ):
                        check = _check_against_scipy(loaded_points, state)
                        if not check.valid:
                            raise AssertionError(
                                f"Le moteur {engine_name} ne correspond pas à SciPy : "
                                f"volume={check.volume_error:.3e}, "
                                f"aire={check.area_error:.3e}."
                            )

                    elif reference_check and timed_result.completed:
                        print(
                            "  validation SciPy ignorée : "
                            f"{requested_points} points dépassent la limite "
                            f"{reference_max_points}."
                        )

                    if engine_name == "scipy" and timed_result.completed:
                        scipy_seconds_for_speedup = timed_result.hull_seconds

                    row: dict[str, object] = {
                        "benchmark_id": uuid.uuid4().hex[:12],
                        "distribution": distribution,
                        "engine": ENGINE_DATABASE_NAMES[engine_name],
                        "requested_points": requested_points,
                        "processed_points": timed_result.processed_points,
                        "completed": timed_result.completed,
                        "generation_seconds": generation_seconds,
                        "database_write_seconds": database_write_seconds,
                        "database_read_seconds": database_read_seconds,
                        "shared_setup_seconds": shared_setup_seconds,
                        "hull_seconds": timed_result.hull_seconds,
                        "total_seconds": shared_setup_seconds + timed_result.hull_seconds,
                        "points_per_second": (
                            timed_result.processed_points / timed_result.hull_seconds
                            if timed_result.hull_seconds > 0
                            else 0.0
                        ),
                        "hull_vertices": len(state.points),
                        "hull_faces": len(state.faces),
                        "surface_area": state.area,
                        "volume": state.volume,
                        "reference_valid": check.valid if check else None,
                        "reference_volume_error": check.volume_error if check else None,
                        "reference_area_error": check.area_error if check else None,
                        "speedup_vs_scipy": None,
                        "seed": seed,
                    }
                    rows.append(row)
                    pending_database_rows.append((row, timed_result, check))

                    status = "terminé" if timed_result.completed else "interrompu"
                    print(
                        f"{status} — traités={timed_result.processed_points} — "
                        f"sommets={len(state.points)} — faces={len(state.faces)} — "
                        f"calcul={timed_result.hull_seconds:.3f} s — "
                        f"débit={row['points_per_second']:.1f} points/s"
                    )

                    if check is not None:
                        print(
                            "  validation SciPy : OK — "
                            f"écart volume={check.volume_error:.3e} — "
                            f"écart aire={check.area_error:.3e}"
                        )

                    if not timed_result.completed:
                        skip_larger_sizes[skip_key] = True
                        print(
                            "Les tailles supérieures sont ignorées pour ce moteur "
                            "et cette distribution."
                        )

                # Le facteur d'accélération est ajouté après les deux mesures afin
                # que les résultats restent corrects quel que soit l'ordre choisi.
                if scipy_seconds_for_speedup is not None:
                    for row, _, _ in pending_database_rows:
                        if row["engine"] == "historical_incremental":
                            historical_seconds = float(row["hull_seconds"])
                            if historical_seconds > 0:
                                row["speedup_vs_scipy"] = (
                                    scipy_seconds_for_speedup / historical_seconds
                                )
                                print(
                                    f"  accélération historique vs SciPy : "
                                    f"x{row['speedup_vs_scipy']:.2f}"
                                )

                for row, timed_result, check in pending_database_rows:
                    record_benchmark(
                        connection,
                        benchmark_id=str(row["benchmark_id"]),
                        distribution=distribution,
                        engine=str(row["engine"]),
                        requested_points=requested_points,
                        processed_points=timed_result.processed_points,
                        seed=seed,
                        completed=timed_result.completed,
                        generation_seconds=generation_seconds,
                        database_write_seconds=database_write_seconds,
                        database_read_seconds=database_read_seconds,
                        hull_seconds=timed_result.hull_seconds,
                        total_seconds=float(row["total_seconds"]),
                        hull_vertex_count=len(timed_result.state.points),
                        hull_face_count=len(timed_result.state.faces),
                        surface_area=timed_result.state.area,
                        volume=timed_result.state.volume,
                        reference_valid=check.valid if check else None,
                        reference_volume_error=check.volume_error if check else None,
                        reference_area_error=check.area_error if check else None,
                        speedup_vs_scipy=(
                            float(row["speedup_vs_scipy"])
                            if row["speedup_vs_scipy"] is not None
                            else None
                        ),
                    )
    finally:
        connection.close()

    _write_benchmark_csv(rows, Path(output))
    print(f"\nRésultats enregistrés dans {output} et dans benchmark_results.")


def _compute_timed_hull(
    points: list[PointRecord],
    *,
    engine_name: EngineName,
    max_seconds: float,
    progress_every: int,
    validate_each_step: bool,
) -> TimedHullResult:
    """Construit une enveloppe jusqu'à la fin ou jusqu'à la limite de temps."""

    initial_points, remaining_points = split_initial_tetrahedron(points)
    engine = _create_engine(engine_name, validate_each_step=validate_each_step)
    start = time.perf_counter()
    state = engine.initialize(initial_points)
    processed_points = 4
    completed = True

    for point in remaining_points:
        # Le moteur historique évite de reconstruire voisins et couleurs pendant
        # un benchmark. Ces données sont matérialisées une seule fois à la fin.
        materialize = engine_name == "scipy"
        possible_state = engine.add_point(point, materialize=materialize)
        if possible_state is not None:
            state = possible_state
        processed_points += 1
        elapsed = time.perf_counter() - start

        if progress_every > 0 and processed_points % progress_every == 0:
            if engine_name == "historical":
                vertex_count = engine.vertex_count
                face_count = engine.face_count
            else:
                vertex_count = len(state.points)
                face_count = len(state.faces)
            print(
                f"  {processed_points}/{len(points)} — "
                f"sommets={vertex_count} — faces={face_count} — "
                f"{elapsed:.2f} s"
            )

        if elapsed >= max_seconds:
            completed = False
            break

    state = engine.snapshot()

    hull_seconds = time.perf_counter() - start
    if engine_name == "historical":
        engine.validate_state(
            state,
            check_convexity=len(state.points) <= 5000,
        )
    else:
        engine.validate_state(state)
    return TimedHullResult(
        state=state,
        processed_points=processed_points,
        completed=completed,
        hull_seconds=hull_seconds,
    )


def _check_against_scipy(
    points: list[PointRecord],
    state: HullState,
    *,
    relative_tolerance: float = 1e-9,
) -> ReferenceCheck:
    """Compare une enveloppe terminée à une référence SciPy calculée une seule fois."""

    coordinates = np.vstack([point.coordinates for point in points])
    point_ids = np.array([point.point_id for point in points], dtype=np.int64)
    reference = ConvexHull(coordinates)

    reference_vertex_ids = set(int(value) for value in point_ids[reference.vertices])
    actual_vertex_ids = set(int(value) for value in state.point_ids)
    vertex_ids_equal = reference_vertex_ids == actual_vertex_ids
    face_count_equal = len(reference.simplices) == len(state.faces)
    volume_error = abs(float(reference.volume) - state.volume)
    area_error = abs(float(reference.area) - state.area)

    volume_scale = max(1.0, abs(float(reference.volume)))
    area_scale = max(1.0, abs(float(reference.area)))
    valid = (
        vertex_ids_equal
        and face_count_equal
        and volume_error <= relative_tolerance * volume_scale
        and area_error <= relative_tolerance * area_scale
    )
    return ReferenceCheck(
        valid=valid,
        vertex_ids_equal=vertex_ids_equal,
        face_count_equal=face_count_equal,
        volume_error=volume_error,
        area_error=area_error,
    )


def _write_benchmark_csv(rows: list[dict[str, object]], path: Path) -> None:
    """Écrit un fichier tabulaire directement exploitable dans VS Code."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def command_inspect(database: str) -> None:
    """Affiche les exécutions et benchmarks récents sans client externe."""

    connection = connect_database(database)
    try:
        point_count = connection.execute("SELECT COUNT(*) FROM points").fetchone()[0]
        runs = connection.execute(
            """
            SELECT
                run_id,
                engine,
                status,
                points_processed,
                hull_vertex_count,
                hull_face_count,
                ROUND(surface_area, 6),
                ROUND(volume, 6),
                started_at,
                finished_at
            FROM hull_runs
            ORDER BY started_at DESC
            LIMIT 10
            """
        ).fetchall()
        benchmarks = connection.execute(
            """
            SELECT
                benchmark_id,
                distribution,
                engine,
                requested_points,
                processed_points,
                completed,
                ROUND(hull_seconds, 3),
                ROUND(points_per_second, 1),
                ROUND(speedup_vs_scipy, 2),
                reference_valid,
                hull_vertex_count,
                hull_face_count,
                created_at
            FROM benchmark_results
            ORDER BY created_at DESC
            LIMIT 20
            """
        ).fetchall()
    finally:
        connection.close()

    print(f"Table points : {point_count} lignes")
    print("\nDernières constructions :")
    if runs:
        for row in runs:
            print(row)
    else:
        print("Aucune exécution enregistrée.")

    print("\nDerniers benchmarks :")
    if benchmarks:
        for row in benchmarks:
            print(row)
    else:
        print("Aucun benchmark enregistré.")


def export_results(
    state: HullState,
    output_dir: Path,
    *,
    run_id: str,
    engine_name: EngineName,
) -> None:
    """Exporte le maillage, les tables relationnelles et les métriques finales."""

    output_dir.mkdir(parents=True, exist_ok=True)
    mesh = _state_to_mesh(state)
    mesh.save(output_dir / "final_hull.vtp")

    with (output_dir / "final_vertices.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.writer(stream)
        writer.writerow(["local_vertex_id", "point_id", "x", "y", "z"])
        for local_index, (point_id, coordinates) in enumerate(
            zip(state.point_ids, state.points, strict=True)
        ):
            writer.writerow([local_index, int(point_id), *map(float, coordinates)])

    with (output_dir / "final_faces.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.writer(stream)
        writer.writerow(["face_id", "vertex_a", "vertex_b", "vertex_c", "color"])
        for face_index, face in enumerate(state.faces):
            writer.writerow(
                [face_index, *[int(value) for value in face], int(state.face_colors[face_index])]
            )

    metrics = {
        "run_id": run_id,
        "engine": ENGINE_DATABASE_NAMES[engine_name],
        "points_processed": state.points_processed,
        "hull_vertices": len(state.points),
        "hull_faces": len(state.faces),
        "surface_area": state.area,
        "volume": state.volume,
        "colors_used": sorted(set(int(value) for value in state.face_colors)),
    }
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _records_to_coordinates(records: list[PointRecord]) -> np.ndarray:
    """Convertit une liste de points en tableau NumPy, y compris lorsqu'elle est vide."""

    if not records:
        return np.empty((0, 3), dtype=np.float64)
    return np.vstack([record.coordinates for record in records])


def _state_to_mesh(state: HullState) -> pv.PolyData:
    """Convertit la connectivité triangulaire vers le format de PyVista."""

    pyvista_faces = np.column_stack(
        [np.full(len(state.faces), 3, dtype=np.int64), state.faces]
    ).ravel()
    mesh = pv.PolyData(state.points, pyvista_faces)
    mesh.cell_data["face_color"] = state.face_colors
    mesh.cell_data["face_rgb"] = FACE_RGB[state.face_colors]
    mesh.point_data["point_id"] = state.point_ids
    return mesh


def _horizon_to_polydata(state: HullState) -> pv.PolyData | None:
    """Construit les segments noirs matérialisant l'horizon courant."""

    if not state.horizon_edges:
        return None

    index_by_point_id = {
        int(point_id): local_index
        for local_index, point_id in enumerate(state.point_ids)
    }
    lines: list[int] = []
    for first_id, second_id in state.horizon_edges:
        if first_id not in index_by_point_id or second_id not in index_by_point_id:
            continue
        lines.extend([2, index_by_point_id[first_id], index_by_point_id[second_id]])
    if not lines:
        return None

    polydata = pv.PolyData(state.points)
    polydata.lines = np.array(lines, dtype=np.int64)
    return polydata


def main() -> None:
    """Point d'entrée commun au terminal Linux et aux configurations VS Code."""

    arguments = build_parser().parse_args()
    if arguments.command == "init-db":
        command_init_database(arguments.database, reset=arguments.reset)
    elif arguments.command == "generate":
        command_generate(
            database=arguments.database,
            point_count=arguments.points,
            distribution=arguments.distribution,
            seed=arguments.seed,
        )
    elif arguments.command == "run":
        command_run(
            database=arguments.database,
            output_dir=arguments.output_dir,
            engine_name=arguments.engine,
            delay=arguments.delay,
            display_every=arguments.display_every,
            record_every=arguments.record_every,
            final_only=arguments.final_only,
            validate_each_step=arguments.validate_each_step,
            reference_check=not arguments.skip_reference_check,
            reference_max_points=arguments.reference_max_points,
            headless=arguments.headless,
            hold=not arguments.no_hold,
        )
    elif arguments.command == "demo":
        command_init_database(arguments.database, reset=True)
        command_run(
            database=arguments.database,
            output_dir=arguments.output_dir,
            engine_name=arguments.engine,
            delay=arguments.delay,
            display_every=arguments.display_every,
            record_every=arguments.record_every,
            final_only=arguments.final_only,
            validate_each_step=arguments.validate_each_step,
            reference_check=not arguments.skip_reference_check,
            reference_max_points=arguments.reference_max_points,
            headless=arguments.headless,
            hold=not arguments.no_hold,
        )
    elif arguments.command == "benchmark":
        command_benchmark(
            database=arguments.database,
            sizes=arguments.sizes,
            distributions=arguments.distributions,
            engines=arguments.engines,
            seed=arguments.seed,
            max_seconds=arguments.max_seconds,
            progress_every=arguments.progress_every,
            validate_each_step=arguments.validate_each_step,
            reference_check=not arguments.skip_reference_check,
            reference_max_points=arguments.reference_max_points,
            output=arguments.output,
        )
    elif arguments.command == "generate-file":
        output = generate_source_file(
            output_path=arguments.output,
            point_count=arguments.points,
            distribution=arguments.distribution,
            seed=arguments.seed,
        )
        print(f"Fichier de test créé : {output}")
    elif arguments.command == "process":
        sheet_value = arguments.sheet
        if isinstance(sheet_value, str) and sheet_value.isdigit():
            sheet_value = int(sheet_value)
        process_source_file(
            input_path=arguments.input,
            runs_dir=arguments.runs_dir,
            sheet=sheet_value,
            x_column=arguments.x_column,
            y_column=arguments.y_column,
            z_column=arguments.z_column,
            id_column=arguments.id_column,
            label_column=arguments.label_column,
            engine_name=arguments.engine,
            record_every=arguments.record_every,
            progress_every=arguments.progress_every,
            validate_each_step=arguments.validate_each_step,
            full_convexity_max_vertices=arguments.full_convexity_max_vertices,
            scipy_reference_max_points=arguments.scipy_reference_max_points,
            max_seconds=arguments.max_seconds,
            show=arguments.show,
            create_screenshot=not arguments.no_screenshot,
        )
    elif arguments.command == "inspect":
        command_inspect(arguments.database)


if __name__ == "__main__":
    main()
