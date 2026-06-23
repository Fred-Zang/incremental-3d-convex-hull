#!/usr/bin/env bash
set -euo pipefail

python app.py process \
  --input data/imports/points_exemple.xlsx \
  --sheet Points \
  --engine historical \
  --record-every 50 \
  --progress-every 100 \
  --show
