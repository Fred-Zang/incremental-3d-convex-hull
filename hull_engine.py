from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import combinations

import numpy as np
from numpy.typing import NDArray
from scipy.spatial import ConvexHull, QhullError

from storage import PointRecord

FaceKey = tuple[int, int, int]
EdgeKey = tuple[int, int]
FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]


@dataclass(slots=True)
class HullState:
    """Contient uniquement la frontière convexe utile à l'étape courante."""

    point_ids: IntArray
    points: FloatArray
    faces: IntArray
    neighbors: IntArray
    face_colors: IntArray
    face_keys: tuple[FaceKey, ...]
    area: float
    volume: float
    points_processed: int
    inserted_point_id: int | None
    inserted_point_retained: bool
    added_face_keys: frozenset[FaceKey]
    removed_face_keys: frozenset[FaceKey]
    horizon_edges: tuple[EdgeKey, ...]


class ScipyRebuildHull3D:
    """Moteur de référence : reconstruit l’enveloppe Qhull après chaque point."""

    def __init__(
        self,
        *,
        tolerance: float = 1e-10,
        validate_each_step: bool = True,
    ) -> None:
        self.tolerance = tolerance
        self.validate_each_step = validate_each_step
        self.state: HullState | None = None
        self.engine_name = "scipy_rebuild"

    def initialize(self, initial_points: list[PointRecord]) -> HullState:
        """Construit le tétraèdre initial à partir de quatre points non coplanaires."""

        if len(initial_points) != 4:
            raise ValueError("L'initialisation exige exactement quatre points.")

        point_ids = np.array([point.point_id for point in initial_points], dtype=np.int64)
        coordinates = np.vstack([point.coordinates for point in initial_points])
        state = self._compute_state(
            candidate_ids=point_ids,
            candidate_points=coordinates,
            previous_state=None,
            points_processed=4,
            inserted_point_id=None,
        )
        if self.validate_each_step:
            self.validate_state(state)
        self.state = state
        return state

    def add_point(
        self,
        point: PointRecord,
        *,
        materialize: bool = True,
    ) -> HullState:
        """Ajoute un point. Le paramètre materialize est accepté pour une interface commune."""

        if self.state is None:
            raise RuntimeError("Le moteur doit être initialisé avant l'ajout de points.")

        candidate_ids = np.append(self.state.point_ids, point.point_id)
        candidate_points = np.vstack([self.state.points, point.coordinates])
        state = self._compute_state(
            candidate_ids=candidate_ids,
            candidate_points=candidate_points,
            previous_state=self.state,
            points_processed=self.state.points_processed + 1,
            inserted_point_id=point.point_id,
        )
        if self.validate_each_step:
            self.validate_state(state)

        if state.volume + self.tolerance < self.state.volume:
            raise AssertionError("Le volume de l'enveloppe ne doit jamais diminuer.")

        self.state = state
        return state

    def snapshot(self) -> HullState:
        """Retourne le dernier état complet calculé par SciPy/Qhull."""

        if self.state is None:
            raise RuntimeError("Le moteur doit être initialisé avant la lecture de son état.")
        return self.state

    def _compute_state(
        self,
        *,
        candidate_ids: IntArray,
        candidate_points: FloatArray,
        previous_state: HullState | None,
        points_processed: int,
        inserted_point_id: int | None,
    ) -> HullState:
        """Reconstruit l'enveloppe sur les anciens sommets extrêmes et le nouveau point."""

        try:
            hull = ConvexHull(candidate_points)
        except QhullError as error:
            raise ValueError(
                "Qhull n'a pas pu construire l'enveloppe. "
                "Vérifier les doublons et la coplanarité des points."
            ) from error

        used_candidate_indices = np.unique(hull.simplices.ravel())
        compact_index_by_candidate = {
            int(candidate_index): compact_index
            for compact_index, candidate_index in enumerate(used_candidate_indices)
        }

        compact_points = candidate_points[used_candidate_indices].astype(np.float64, copy=True)
        compact_ids = candidate_ids[used_candidate_indices].astype(np.int64, copy=True)
        compact_faces = np.array(
            [
                [compact_index_by_candidate[int(index)] for index in simplex]
                for simplex in hull.simplices
            ],
            dtype=np.int64,
        )
        compact_faces = _orient_faces_outward(compact_points, compact_faces)

        face_keys = tuple(
            _face_key(candidate_ids[hull.simplices[face_index]])
            for face_index in range(len(hull.simplices))
        )
        old_keys = set(previous_state.face_keys) if previous_state else set()
        new_keys = set(face_keys)
        added_face_keys = frozenset(new_keys - old_keys)
        removed_face_keys = frozenset(old_keys - new_keys)
        horizon_edges = _find_horizon_edges(removed_face_keys)

        previous_colors = (
            {
                key: int(previous_state.face_colors[index])
                for index, key in enumerate(previous_state.face_keys)
            }
            if previous_state
            else {}
        )
        face_colors = _color_faces(
            neighbors=hull.neighbors.astype(np.int64, copy=True),
            face_keys=face_keys,
            previous_colors=previous_colors,
        )

        inserted_point_retained = (
            inserted_point_id is None
            or inserted_point_id in set(int(value) for value in compact_ids)
        )

        return HullState(
            point_ids=compact_ids,
            points=compact_points,
            faces=compact_faces,
            neighbors=hull.neighbors.astype(np.int64, copy=True),
            face_colors=face_colors,
            face_keys=face_keys,
            area=float(hull.area),
            volume=float(hull.volume),
            points_processed=points_processed,
            inserted_point_id=inserted_point_id,
            inserted_point_retained=inserted_point_retained,
            added_face_keys=added_face_keys,
            removed_face_keys=removed_face_keys,
            horizon_edges=horizon_edges,
        )

    def validate_state(self, state: HullState) -> None:
        """Vérifie les invariants combinatoires du polyèdre convexe triangulé."""

        if state.points.ndim != 2 or state.points.shape[1] != 3:
            raise AssertionError("Les sommets doivent former un tableau de taille (n, 3).")
        if state.faces.ndim != 2 or state.faces.shape[1] != 3:
            raise AssertionError("Toutes les faces doivent être triangulaires.")
        if len(state.face_keys) != len(state.faces):
            raise AssertionError("Chaque triangle doit posséder une clé de face.")
        if len(set(state.face_keys)) != len(state.face_keys):
            raise AssertionError("Deux faces identiques ont été détectées.")

        edge_counts: Counter[EdgeKey] = Counter()
        for face in state.faces:
            for first, second in combinations((int(v) for v in face), 2):
                edge_counts[tuple(sorted((first, second)))] += 1

        if not edge_counts or any(count != 2 for count in edge_counts.values()):
            raise AssertionError("Chaque arête d'une surface fermée doit appartenir à deux faces.")

        vertex_count = len(state.points)
        edge_count = len(edge_counts)
        face_count = len(state.faces)
        if vertex_count - edge_count + face_count != 2:
            raise AssertionError("La formule d'Euler V - E + F = 2 n'est pas respectée.")

        for face_index, neighbors in enumerate(state.neighbors):
            for neighbor_index in neighbors:
                if neighbor_index < 0:
                    raise AssertionError("Une enveloppe 3D fermée ne doit pas avoir de bord libre.")
                if state.face_colors[face_index] == state.face_colors[neighbor_index]:
                    raise AssertionError("Deux faces voisines partagent la même couleur.")

        if np.any(state.face_colors < 0) or np.any(state.face_colors > 3):
            raise AssertionError("La coloration doit utiliser uniquement quatre couleurs.")
        if state.volume <= 0.0 or state.area <= 0.0:
            raise AssertionError("L'aire et le volume doivent être strictement positifs.")



# Alias conservé pour la compatibilité avec les versions v1 à v3.
IncrementalHull3D = ScipyRebuildHull3D

def split_initial_tetrahedron(
    points: list[PointRecord],
    *,
    tolerance: float = 1e-10,
) -> tuple[list[PointRecord], list[PointRecord]]:
    """Trouve les quatre premiers points non coplanaires sans perdre l'ordre du flux."""

    if len(points) < 4:
        raise ValueError("Au moins quatre points sont nécessaires.")

    first_index = 0
    first = points[first_index].coordinates

    second_index: int | None = None
    for index in range(1, len(points)):
        if np.linalg.norm(points[index].coordinates - first) > tolerance:
            second_index = index
            break
    if second_index is None:
        raise ValueError("Tous les points sont confondus.")

    second = points[second_index].coordinates
    direction = second - first
    third_index: int | None = None
    for index in range(second_index + 1, len(points)):
        candidate = points[index].coordinates
        if np.linalg.norm(np.cross(direction, candidate - first)) > tolerance:
            third_index = index
            break
    if third_index is None:
        raise ValueError("Tous les points sont alignés.")

    third = points[third_index].coordinates
    fourth_index: int | None = None
    for index in range(third_index + 1, len(points)):
        candidate = points[index].coordinates
        signed_volume_times_six = np.linalg.det(
            np.vstack([second - first, third - first, candidate - first])
        )
        if abs(signed_volume_times_six) > tolerance:
            fourth_index = index
            break
    if fourth_index is None:
        raise ValueError("Tous les points sont coplanaires.")

    selected_indices = {first_index, second_index, third_index, fourth_index}
    initial = [points[index] for index in sorted(selected_indices)]
    remaining = [point for index, point in enumerate(points) if index not in selected_indices]
    return initial, remaining


def _face_key(point_ids: IntArray) -> FaceKey:
    """Produit une identité de face indépendante de son orientation géométrique."""

    values = sorted(int(value) for value in point_ids)
    return values[0], values[1], values[2]


def _find_horizon_edges(removed_faces: frozenset[FaceKey]) -> tuple[EdgeKey, ...]:
    """Extrait la frontière entre les faces supprimées et les faces conservées."""

    edge_counts: Counter[EdgeKey] = Counter()
    for face in removed_faces:
        for first, second in combinations(face, 2):
            edge_counts[tuple(sorted((first, second)))] += 1
    return tuple(sorted(edge for edge, count in edge_counts.items() if count == 1))


def _color_faces(
    *,
    neighbors: IntArray,
    face_keys: tuple[FaceKey, ...],
    previous_colors: dict[FaceKey, int],
) -> IntArray:
    """Préserve les couleurs valides puis colore les nouvelles faces avec quatre choix."""

    colors = np.full(len(face_keys), -1, dtype=np.int64)
    for index, key in enumerate(face_keys):
        if key in previous_colors:
            colors[index] = previous_colors[key]

    # Chaque triangle possède au plus trois voisins. Un parcours linéaire suffit
    # donc : au moment de colorier une face, au plus trois couleurs sont interdites.
    # Cette version évite la recherche quadratique de la prochaine face et reste
    # adaptée aux enveloppes contenant plusieurs milliers de triangles.
    for face_index, current_color in enumerate(colors):
        if current_color >= 0:
            continue
        forbidden = {
            int(colors[neighbor])
            for neighbor in neighbors[face_index]
            if neighbor >= 0 and colors[neighbor] >= 0
        }
        for color in range(4):
            if color not in forbidden:
                colors[face_index] = color
                break
        else:
            raise AssertionError("La coloration gloutonne a dépassé quatre couleurs.")

    return colors


def _orient_faces_outward(points: FloatArray, faces: IntArray) -> IntArray:
    """Oriente les triangles vers l'extérieur pour un rendu cohérent."""

    oriented = faces.copy()
    hull_center = points.mean(axis=0)
    for index, face in enumerate(oriented):
        first, second, third = points[face]
        normal = np.cross(second - first, third - first)
        face_center = (first + second + third) / 3.0
        if np.dot(normal, face_center - hull_center) < 0.0:
            oriented[index, 1], oriented[index, 2] = oriented[index, 2], oriented[index, 1]
    return oriented
