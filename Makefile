.PHONY: help install test lint demo sample benchmark clean

help:
	@echo "Commandes disponibles :"
	@echo "  make install    Installer les dépendances de développement"
	@echo "  make test       Lancer les tests"
	@echo "  make lint       Vérifier le code avec Ruff"
	@echo "  make demo       Lancer la démonstration historique"
	@echo "  make sample     Traiter le fichier Excel d'exemple"
	@echo "  make benchmark  Comparer les moteurs sur 1000, 1500 et 2000 points"
	@echo "  make clean      Supprimer les caches locaux"

install:
	python -m pip install --upgrade pip
	python -m pip install -r requirements-dev.txt

test:
	python -m pytest

lint:
	ruff check .

demo:
	python app.py demo --engine historical --delay 0.8

sample:
	python app.py process --input data/imports/points_exemple.xlsx --sheet Points --engine historical --show

benchmark:
	python app.py benchmark --sizes 1000 1500 2000 --distributions sphere --engines scipy historical --max-seconds 300 --progress-every 250

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache
