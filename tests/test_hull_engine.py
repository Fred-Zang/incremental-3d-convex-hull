from __future__ import annotations

import numpy as np
import pytest
from scipy.spatial import ConvexHull

from hull_engine import IncrementalHull3D, split_initial_tetrahedron
from storage import DEMO_POINTS


def test_incremental_result_matches_direct_scipy_hull() -> None:
    """Le résultat progressif doit être identique au calcul direct de référence."""

    points = list(DEMO_POINTS)
    initial, remaining = split_initial_tetrahedron(points)
    engine = IncrementalHull3D()
    state = engine.initialize(initial)
    volumes = [state.volume]

    for point in remaining:
        state = engine.add_point(point)
        volumes.append(state.volume)

    direct_points = np.vstack([point.coordinates for point in points])
    reference = ConvexHull(direct_points)
    reference_ids = {points[index].point_id for index in reference.vertices}

    assert set(int(value) for value in state.point_ids) == reference_ids
    assert state.volume == pytest.approx(reference.volume, rel=1e-12, abs=1e-12)
    assert state.area == pytest.approx(reference.area, rel=1e-12, abs=1e-12)
    assert all(next_volume >= volume - 1e-12 for volume, next_volume in zip(volumes, volumes[1:], strict=False))


def test_adjacent_faces_never_share_a_color() -> None:
    """La coloration de la surface doit rester valide à chaque insertion."""

    initial, remaining = split_initial_tetrahedron(list(DEMO_POINTS))
    engine = IncrementalHull3D()
    states = [engine.initialize(initial)]
    states.extend(engine.add_point(point) for point in remaining)

    for state in states:
        assert set(int(value) for value in state.face_colors) <= {0, 1, 2, 3}
        for face_index, neighbors in enumerate(state.neighbors):
            for neighbor_index in neighbors:
                assert state.face_colors[face_index] != state.face_colors[neighbor_index]


def test_known_inner_point_is_not_retained() -> None:
    """Le centre du tétraèdre initial ne doit jamais devenir un sommet extrême."""

    initial, remaining = split_initial_tetrahedron(list(DEMO_POINTS))
    engine = IncrementalHull3D()
    engine.initialize(initial)

    center_point = next(point for point in remaining if point.point_id == 5)
    state = engine.add_point(center_point)

    assert not state.inserted_point_retained
    assert 5 not in set(int(value) for value in state.point_ids)
    assert not state.added_face_keys
    assert not state.removed_face_keys
