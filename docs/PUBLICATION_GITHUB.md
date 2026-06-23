# Publier le projet sur GitHub

Nom de dépôt recommandé :

```text
incremental-3d-convex-hull
```

Description courte recommandée :

```text
Incremental 3D convex hull from Excel, CSV or Parquet point clouds, with DuckDB traceability, SciPy validation and PyVista visualization.
```

## 1. Vérification locale

Depuis la racine du projet :

```bash
source .venv/bin/activate
python -m pytest
ruff check .
git status
```

## 2. Initialiser Git

```bash
git init
git add .
git commit -m "Initial public release: incremental 3D convex hull v5"
git branch -M main
```

## 3. Publication avec GitHub CLI

Se connecter une seule fois :

```bash
gh auth login
```

Créer le dépôt public et pousser le contenu :

```bash
gh repo create incremental-3d-convex-hull \
    --public \
    --source=. \
    --remote=origin \
    --push
```

Cette méthode évite d’avoir à saisir manuellement l’URL du dépôt.

## 4. Publication depuis le site GitHub

Créer un dépôt vide nommé `incremental-3d-convex-hull`. Ne pas demander à GitHub de générer un README, une licence ou un `.gitignore`, car ces fichiers existent déjà localement.

Puis relier le dépôt local :

```bash
git remote add origin ADRESSE_DU_DEPOT_GITHUB
git push -u origin main
```

## 5. Configuration recommandée du dépôt

Dans la page du dépôt :

- ajouter la description courte proposée plus haut ;
- activer la section **Issues** ;
- définir `preview.png` comme image sociale dans les paramètres ;
- ajouter les topics suivants :

```text
python
computational-geometry
convex-hull
point-cloud
3d
scientific-computing
duckdb
pyvista
scipy
data-visualization
```

## 6. Première version publiée

Créer un tag local :

```bash
git tag -a v5.0.0 -m "Pipeline scientifique complet v5"
git push origin v5.0.0
```

Puis créer une release GitHub à partir de ce tag avec le titre :

```text
v5.0.0 — Pipeline scientifique complet
```

Résumé recommandé :

```text
Première version publique complète : import Excel/CSV/Parquet, contrôle qualité,
base DuckDB par exécution, moteur historique incrémental, validation SciPy/Qhull,
exports scientifiques, visualisation PyVista et documentation mathématique.
```

## 7. Vérifications après publication

- le README affiche correctement `preview.png` ;
- le workflow GitHub Actions est vert ;
- la licence MIT est détectée ;
- le fichier `CITATION.cff` produit une section « Cite this repository » ;
- aucun fichier `.duckdb`, environnement virtuel ou résultat volumineux n’est versionné ;
- le fichier Excel d’exemple reste téléchargeable.

## Documentation officielle

```text
https://docs.github.com/repositories/creating-and-managing-repositories/creating-a-new-repository
```

```text
https://docs.github.com/get-started/importing-your-projects-to-github/importing-source-code-to-github/adding-locally-hosted-code-to-github
```
