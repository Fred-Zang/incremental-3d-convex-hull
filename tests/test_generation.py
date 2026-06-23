from __future__ import annotations

import numpy as np

from storage import generate_points


def test_volume_points_are_inside_unit_ball() -> None:
    """La distribution volumique doit rester dans la boule unité."""

    points = generate_points(200, distribution="volume", seed=42)
    coordinates = np.vstack([point.coordinates for point in points])
    assert np.all(np.linalg.norm(coordinates, axis=1) <= 1.0 + 1e-12)


def test_sphere_points_are_on_unit_sphere() -> None:
    """Le cas difficile doit placer tous les points sur la sphère unité."""

    points = generate_points(200, distribution="sphere", seed=42)
    coordinates = np.vstack([point.coordinates for point in points])
    assert np.allclose(np.linalg.norm(coordinates, axis=1), 1.0)


def test_generation_is_reproducible() -> None:
    """Une même graine doit produire exactement le même nuage."""

    first = generate_points(20, distribution="cube", seed=7)
    second = generate_points(20, distribution="cube", seed=7)
    assert np.array_equal(
        np.vstack([point.coordinates for point in first]),
        np.vstack([point.coordinates for point in second]),
    )
