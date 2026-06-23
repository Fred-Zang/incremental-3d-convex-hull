# Incremental 3D Convex Hull

Python reconstruction of a computational-geometry project first implemented in Super-Pascal in 1991.

The application imports real point clouds from Excel, CSV or Parquet, validates and stores them in DuckDB, incrementally builds a triangulated 3D convex hull, validates the result against SciPy/Qhull when feasible, exports scientific artifacts and opens an interactive PyVista rendering.

The custom historical engine updates only the visible region of the current hull: visible-face detection, horizon extraction, local face deletion and cap construction.

## Local benchmark

| Points on a sphere | Full SciPy rebuild per insertion | Historical local engine | Speed-up |
|---:|---:|---:|---:|
| 1,000 | 45.918 s | 0.774 s | ×59.34 |
| 1,500 | 102.594 s | 1.354 s | ×75.77 |
| 2,000 | 181.721 s | 2.077 s | ×87.49 |

All reference validations passed, with surface-area and volume differences below a few `10⁻¹⁴`.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pytest

python app.py process \
    --input data/imports/points_exemple.xlsx \
    --sheet Points \
    --engine historical \
    --show
```

The full French documentation is available in [`README.md`](README.md) and [`MEMO_TECHNIQUE_V5.md`](MEMO_TECHNIQUE_V5.md).

## License

MIT License.
