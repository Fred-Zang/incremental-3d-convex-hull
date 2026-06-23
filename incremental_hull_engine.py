from __future__ import annotations

from collections import Counter
from itertools import combinations

import numpy as np
from numpy.typing import NDArray

from hull_engine import (
    EdgeKey,
    FaceKey,
    HullState,
    _color_faces,
    _face_key,
)
from storage import PointRecord

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]


class HistoricalIncrementalHull3D:
    """Met à jour localement une enveloppe 3D par faces visibles et horizon.

    Ce moteur n'appelle jamais SciPy/Qhull après l'initialisation. Pour chaque
    nouveau point, il conserve les faces non visibles, supprime les faces
    visibles, extrait l'horizon puis crée le capuchon reliant cet horizon au
    nouveau sommet.
    """

    def __init__(
        self,
        *,
        tolerance: float = 1e-10,
        validate_each_step: bool = True,
    ) -> None:
        self.tolerance = tolerance
        self.validate_each_step = validate_each_step
        self.engine_name = "historical_incremental"

        self._point_ids = np.empty(0, dtype=np.int64)
        self._points = np.empty((0, 3), dtype=np.float64)
        self._faces = np.empty((0, 3), dtype=np.int64)
        self._normals = np.empty((0, 3), dtype=np.float64)
        self._offsets = np.empty(0, dtype=np.float64)
        self._face_areas = np.empty(0, dtype=np.float64)
        self._face_volumes = np.empty(0, dtype=np.float64)

        self._points_processed = 0
        self._inserted_point_id: int | None = None
        self._inserted_point_retained = False
        self._added_face_keys: frozenset[FaceKey] = frozenset()
        self._removed_face_keys: frozenset[FaceKey] = frozenset()
        self._horizon_edges: tuple[EdgeKey, ...] = ()
        self._previous_colors: dict[FaceKey, int] = {}
        self.state: HullState | None = None

    def initialize(self, initial_points: list[PointRecord]) -> HullState:
        """Construit les quatre faces du tétraèdre initial sans Qhull."""

        if len(initial_points) != 4:
            raise ValueError("L'initialisation exige exactement quatre points.")

        self._point_ids = np.array(
            [point.point_id for point in initial_points], dtype=np.int64
        )
        self._points = np.vstack([point.coordinates for point in initial_points])

        raw_faces = np.array(
            [
                [0, 1, 2],
                [0, 3, 1],
                [0, 2, 3],
                [1, 3, 2],
            ],
            dtype=np.int64,
        )
        interior_reference = self._points.mean(axis=0)
        self._faces = _orient_faces_with_interior(
            self._points,
            raw_faces,
            interior_reference,
            tolerance=self.tolerance,
        )
        face_count = len(self._faces)
        self._normals = np.empty((face_count, 3), dtype=np.float64)
        self._offsets = np.empty(face_count, dtype=np.float64)
        self._face_areas = np.empty(face_count, dtype=np.float64)
        self._face_volumes = np.empty(face_count, dtype=np.float64)
        self._refresh_face_geometry()

        self._points_processed = 4
        self._inserted_point_id = None
        self._inserted_point_retained = True
        self._added_face_keys = frozenset(
            _face_key(self._point_ids[face]) for face in self._faces
        )
        self._removed_face_keys = frozenset()
        self._horizon_edges = ()

        state = self.snapshot()
        if self.validate_each_step:
            self.validate_state(state)
        return state

    def add_point(
        self,
        point: PointRecord,
        *,
        materialize: bool = True,
    ) -> HullState | None:
        """Ajoute un point en ne modifiant que la zone visible de la frontière."""

        if self._faces.size == 0:
            raise RuntimeError("Le moteur doit être initialisé avant l'ajout de points.")

        coordinates = point.coordinates
        signed_distances = self._normals @ coordinates + self._offsets
        visible_mask = signed_distances > self.tolerance
        visible_indices = np.flatnonzero(visible_mask)

        self._points_processed += 1
        self._inserted_point_id = point.point_id

        if len(visible_indices) == 0:
            self._inserted_point_retained = False
            self._added_face_keys = frozenset()
            self._removed_face_keys = frozenset()
            self._horizon_edges = ()
            if materialize:
                state = self.snapshot()
                if self.validate_each_step:
                    self.validate_state(state)
                return state
            return None

        old_volume = self.volume
        visible_faces = self._faces[visible_indices]
        removed_face_keys = frozenset(
            _face_key(self._point_ids[face]) for face in visible_faces
        )
        horizon_local_edges = _extract_horizon_edges(visible_faces)
        if len(horizon_local_edges) < 3:
            raise AssertionError("L'horizon doit contenir au moins trois arêtes.")
        horizon_point_edges = tuple(
            sorted(
                (
                    min(int(self._point_ids[first]), int(self._point_ids[second])),
                    max(int(self._point_ids[first]), int(self._point_ids[second])),
                )
                for first, second in horizon_local_edges
            )
        )

        interior_reference = self._points.mean(axis=0)
        new_local_index = len(self._points)
        self._point_ids = np.append(self._point_ids, point.point_id)
        self._points = np.vstack([self._points, coordinates])

        new_faces = np.column_stack(
            [
                horizon_local_edges[:, 0],
                horizon_local_edges[:, 1],
                np.full(len(horizon_local_edges), new_local_index, dtype=np.int64),
            ]
        )
        new_faces = _orient_faces_with_interior(
            self._points,
            new_faces,
            interior_reference,
            tolerance=self.tolerance,
        )

        keep_mask = ~visible_mask
        self._faces = np.vstack([self._faces[keep_mask], new_faces])
        self._normals = np.vstack(
            [self._normals[keep_mask], np.empty((len(new_faces), 3), dtype=np.float64)]
        )
        self._offsets = np.concatenate(
            [self._offsets[keep_mask], np.empty(len(new_faces), dtype=np.float64)]
        )
        self._face_areas = np.concatenate(
            [self._face_areas[keep_mask], np.empty(len(new_faces), dtype=np.float64)]
        )
        self._face_volumes = np.concatenate(
            [self._face_volumes[keep_mask], np.empty(len(new_faces), dtype=np.float64)]
        )
        self._refresh_face_geometry(start_index=int(np.count_nonzero(keep_mask)))
        self._compact_unreferenced_vertices()

        self._inserted_point_retained = True
        self._removed_face_keys = removed_face_keys
        self._added_face_keys = frozenset(
            _face_key(self._point_ids[face])
            for face in self._faces[-len(new_faces) :]
        )
        self._horizon_edges = horizon_point_edges

        if self.volume + self.tolerance < old_volume:
            raise AssertionError("Le volume de l'enveloppe ne doit jamais diminuer.")

        if materialize:
            state = self.snapshot()
            if self.validate_each_step:
                self.validate_state(state)
            return state
        return None

    @property
    def area(self) -> float:
        """Retourne l'aire courante sans reconstruire la topologie."""

        return float(np.sum(self._face_areas))

    @property
    def volume(self) -> float:
        """Retourne le volume courant depuis les contributions orientées des faces."""

        return float(abs(np.sum(self._face_volumes)))

    @property
    def vertex_count(self) -> int:
        return int(len(self._points))

    @property
    def face_count(self) -> int:
        return int(len(self._faces))

    @property
    def points_processed(self) -> int:
        return self._points_processed

    @property
    def inserted_point_retained(self) -> bool:
        return self._inserted_point_retained

    def snapshot(self) -> HullState:
        """Matérialise voisins, clés et couleurs pour affichage, audit ou validation."""

        if self._faces.size == 0:
            raise RuntimeError("Le moteur doit être initialisé avant la lecture de son état.")

        neighbors = _build_neighbors(self._faces)
        face_keys = tuple(_face_key(self._point_ids[face]) for face in self._faces)
        face_colors = _color_faces(
            neighbors=neighbors,
            face_keys=face_keys,
            previous_colors=self._previous_colors,
        )
        self._previous_colors = {
            key: int(face_colors[index]) for index, key in enumerate(face_keys)
        }

        state = HullState(
            point_ids=self._point_ids.copy(),
            points=self._points.copy(),
            faces=self._faces.copy(),
            neighbors=neighbors,
            face_colors=face_colors,
            face_keys=face_keys,
            area=self.area,
            volume=self.volume,
            points_processed=self._points_processed,
            inserted_point_id=self._inserted_point_id,
            inserted_point_retained=self._inserted_point_retained,
            added_face_keys=self._added_face_keys,
            removed_face_keys=self._removed_face_keys,
            horizon_edges=self._horizon_edges,
        )
        self.state = state
        return state

    def validate_state(
        self,
        state: HullState,
        *,
        check_convexity: bool = True,
    ) -> None:
        """Vérifie la topologie et, sur demande, toute la convexité géométrique."""

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
            raise AssertionError("Chaque arête doit appartenir exactement à deux faces.")

        if len(state.points) - len(edge_counts) + len(state.faces) != 2:
            raise AssertionError("La formule d'Euler V - E + F = 2 n'est pas respectée.")

        if np.any(state.neighbors < 0):
            raise AssertionError("Une enveloppe fermée ne doit pas avoir de bord libre.")
        for face_index, neighbors in enumerate(state.neighbors):
            for neighbor_index in neighbors:
                if state.face_colors[face_index] == state.face_colors[neighbor_index]:
                    raise AssertionError("Deux faces voisines partagent la même couleur.")

        if check_convexity:
            normals, offsets, _, _ = _face_geometry(state.points, state.faces)
            scale = max(1.0, float(np.max(np.linalg.norm(state.points, axis=1))))
            allowed = max(self.tolerance * 100.0, 1e-9 * scale)

            # Le calcul est réalisé par blocs pour éviter de construire une matrice
            # V × F gigantesque sur les grands benchmarks.
            for start in range(0, len(state.points), 512):
                point_block = state.points[start : start + 512]
                signed = point_block @ normals.T + offsets[None, :]
                if float(np.max(signed)) > allowed:
                    raise AssertionError(
                        "Au moins un sommet se trouve à l'extérieur d'une face."
                    )

        if state.area <= 0.0 or state.volume <= 0.0:
            raise AssertionError("L'aire et le volume doivent être strictement positifs.")

    def _refresh_face_geometry(self, *, start_index: int = 0) -> None:
        """Calcule normales, plans, aires et volumes des faces nouvelles ou initiales."""

        if start_index < 0 or start_index > len(self._faces):
            raise ValueError("Indice de recalcul des faces invalide.")
        if start_index == len(self._faces):
            return

        normals, offsets, areas, volumes = _face_geometry(
            self._points,
            self._faces[start_index:],
        )
        self._normals[start_index:] = normals
        self._offsets[start_index:] = offsets
        self._face_areas[start_index:] = areas
        self._face_volumes[start_index:] = volumes

    def _compact_unreferenced_vertices(self) -> None:
        """Supprime les anciens sommets qui ne participent plus à aucune face."""

        used_indices = np.unique(self._faces.ravel())
        if len(used_indices) == len(self._points):
            return

        remap = np.full(len(self._points), -1, dtype=np.int64)
        remap[used_indices] = np.arange(len(used_indices), dtype=np.int64)
        self._point_ids = self._point_ids[used_indices]
        self._points = self._points[used_indices]
        self._faces = remap[self._faces]


def _extract_horizon_edges(visible_faces: IntArray) -> IntArray:
    """Retourne les arêtes présentes une seule fois dans la zone visible."""

    edges = np.vstack(
        [
            visible_faces[:, [0, 1]],
            visible_faces[:, [1, 2]],
            visible_faces[:, [2, 0]],
        ]
    )
    edges = np.sort(edges, axis=1)
    unique_edges, counts = np.unique(edges, axis=0, return_counts=True)
    return unique_edges[counts == 1].astype(np.int64, copy=False)


def _orient_faces_with_interior(
    points: FloatArray,
    faces: IntArray,
    interior_reference: FloatArray,
    *,
    tolerance: float,
) -> IntArray:
    """Oriente toutes les faces pour que leur normale s'éloigne d'un point intérieur."""

    oriented = faces.astype(np.int64, copy=True)
    triangles = points[oriented]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    norm_values = np.linalg.norm(normals, axis=1)
    if np.any(norm_values <= tolerance):
        raise ValueError("Une face dégénérée a été produite pendant l'insertion.")

    toward_interior = np.einsum(
        "ij,ij->i",
        normals,
        interior_reference[None, :] - triangles[:, 0],
    )
    flip_indices = np.flatnonzero(toward_interior > 0.0)
    if len(flip_indices):
        temp = oriented[flip_indices, 0].copy()
        oriented[flip_indices, 0] = oriented[flip_indices, 1]
        oriented[flip_indices, 1] = temp
    return oriented


def _face_geometry(
    points: FloatArray,
    faces: IntArray,
) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray]:
    """Calcule les plans unitaires, les aires et les volumes orientés des triangles."""

    triangles = points[faces]
    cross_products = np.cross(
        triangles[:, 1] - triangles[:, 0],
        triangles[:, 2] - triangles[:, 0],
    )
    doubled_areas = np.linalg.norm(cross_products, axis=1)
    normals = cross_products / doubled_areas[:, None]
    offsets = -np.einsum("ij,ij->i", normals, triangles[:, 0])
    areas = 0.5 * doubled_areas
    volumes = np.einsum(
        "ij,ij->i",
        triangles[:, 0],
        np.cross(triangles[:, 1], triangles[:, 2]),
    ) / 6.0
    return normals, offsets, areas, volumes


def _build_neighbors(faces: IntArray) -> IntArray:
    """Construit les trois voisins de chaque triangle à partir des arêtes partagées."""

    neighbors = np.full((len(faces), 3), -1, dtype=np.int64)
    edge_owner: dict[EdgeKey, tuple[int, int]] = {}

    # La case 0 est opposée au sommet 0 et correspond donc à l'arête (1, 2),
    # comme dans la convention utilisée par scipy.spatial.ConvexHull.neighbors.
    edge_slots = ((1, 2), (2, 0), (0, 1))
    for face_index, face in enumerate(faces):
        for slot, (first_position, second_position) in enumerate(edge_slots):
            edge = tuple(
                sorted(
                    (
                        int(face[first_position]),
                        int(face[second_position]),
                    )
                )
            )
            previous = edge_owner.pop(edge, None)
            if previous is None:
                edge_owner[edge] = (face_index, slot)
            else:
                other_face, other_slot = previous
                neighbors[face_index, slot] = other_face
                neighbors[other_face, other_slot] = face_index

    if edge_owner:
        raise AssertionError("La surface contient au moins une arête non appariée.")
    return neighbors
