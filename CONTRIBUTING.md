# Contribuer

Merci de l’intérêt porté au projet.

## Préparer l’environnement

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

## Vérifications locales

```bash
python -m pytest
ruff check .
```

## Principes attendus

- conserver une séparation claire entre import, stockage, géométrie et rendu ;
- ajouter un test pour toute correction ou évolution algorithmique ;
- ne jamais ignorer silencieusement une ligne invalide ;
- préserver les identifiants sources dans les exports ;
- documenter les tolérances numériques ;
- comparer au moteur SciPy/Qhull lorsque la taille du jeu le permet ;
- éviter de versionner les bases DuckDB et les résultats volumineux générés.

## Proposition de modification

1. créer une branche dédiée ;
2. expliquer le problème et la solution ;
3. ajouter ou adapter les tests ;
4. vérifier que les exports restent reproductibles ;
5. ouvrir une pull request avec les résultats obtenus.
