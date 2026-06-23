from __future__ import annotations

import numpy as np
import pytest
from scipy.spatial import ConvexHull

from hull_engine import ScipyRebuildHull3D, split_initial_tetrahedron
from incremental_hull_engine import HistoricalIncrementalHull3D
from storage import DEMO_POINTS, generate_points


def _run_historical(points):
    initial, remaining = split_initial_tetrahedron(points)
    engine = HistoricalIncrementalHull3D(validate_each_step=False)
    engine.initialize(initial)
    for point in remaining:
        engine.add_point(point, materialize=False)
    state = engine.snapshot()
    engine.validate_state(state)
    return state


def _run_scipy(points):
    initial, remaining = split_initial_tetrahedron(points)
    engine = ScipyRebuildHull3D(validate_each_step=False)
    state = engine.initialize(initial)
    for point in remaining:
        state = engine.add_point(point)
    engine.validate_state(state)
    return state


@pytest.mark.parametrize(
    ("distribution", "count", "seed"),
    [
        ("sphere", 100, 42),
        ("volume", 200, 42),
        ("cube", 200, 7),
    ],
)
def test_historical_engine_matches_scipy(
    distribution: str,
    count: int,
    seed: int,
) -> None:
    """Les deux moteurs doivent produire exactement la même frontière finale."""

    points = generate_points(count, distribution=distribution, seed=seed)
    historical = _run_historical(points)
    scipy_state = _run_scipy(points)

    assert set(map(int, historical.point_ids)) == set(map(int, scipy_state.point_ids))
    assert set(historical.face_keys) == set(scipy_state.face_keys)
    assert historical.volume == pytest.approx(scipy_state.volume, rel=1e-12, abs=1e-12)
    assert historical.area == pytest.approx(scipy_state.area, rel=1e-12, abs=1e-12)


def test_historical_engine_matches_direct_reference_on_demo() -> None:
    """Le jeu historique de vingt points doit rester identique à Qhull."""

    points = list(DEMO_POINTS)
    state = _run_historical(points)
    coordinates = np.vstack([point.coordinates for point in points])
    reference = ConvexHull(coordinates)

    assert len(state.points) == 18
    assert len(state.faces) == 32
    assert state.volume == pytest.approx(reference.volume, rel=1e-12, abs=1e-12)
    assert state.area == pytest.approx(reference.area, rel=1e-12, abs=1e-12)


def test_historical_sphere_has_expected_triangulated_face_count() -> None:
    """Une sphère aléatoire sans coplanarité suit F = 2V - 4."""

    state = _run_historical(generate_points(250, distribution="sphere", seed=42))
    assert len(state.points) == 250
    assert len(state.faces) == 2 * len(state.points) - 4


def test_historical_rejects_known_inner_point_without_changing_mesh() -> None:
    """Un point intérieur ne doit créer ni face ni sommet supplémentaire."""

    initial, remaining = split_initial_tetrahedron(list(DEMO_POINTS))
    engine = HistoricalIncrementalHull3D()
    before = engine.initialize(initial)
    center = next(point for point in remaining if point.point_id == 5)
    after = engine.add_point(center)

    assert after is not None
    assert not after.inserted_point_retained
    assert np.array_equal(before.point_ids, after.point_ids)
    assert np.array_equal(before.faces, after.faces)
    assert not after.added_face_keys
    assert not after.removed_face_keys
