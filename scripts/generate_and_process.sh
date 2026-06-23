#!/usr/bin/env bash
set -euo pipefail

POINTS="${1:-10000}"
DISTRIBUTION="${2:-volume}"
SOURCE="data/imports/test_${DISTRIBUTION}_${POINTS}.parquet"

python app.py generate-file \
  --points "$POINTS" \
  --distribution "$DISTRIBUTION" \
  --seed 42 \
  --output "$SOURCE"

python app.py process \
  --input "$SOURCE" \
  --engine historical \
  --record-every 1000 \
  --progress-every 1000 \
  --show
