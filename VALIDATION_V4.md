# Validation technique de la v4

## Résultat fonctionnel

Le moteur `historical_incremental` a été comparé au moteur `scipy_rebuild` sur :

- les vingt points de démonstration ;
- 100 points sur une sphère ;
- 200 points dans le volume d'une boule ;
- 200 points dans un cube.

Pour chaque cas, les deux moteurs produisent :

- le même ensemble de sommets extrêmes ;
- le même ensemble de faces triangulaires ;
- la même aire à la tolérance numérique près ;
- le même volume à la tolérance numérique près.

Les douze tests automatisés réussissent.

## Mesures indicatives sur l'environnement de validation

Cas difficile : tous les points sont générés sur la sphère unité.

| Points | Moteur | Temps géométrique | Débit | Sommets | Faces | Validation finale |
|---:|---|---:|---:|---:|---:|---|
| 100 | SciPy global | 0,426 s | 234,8 points/s | 100 | 196 | OK |
| 100 | Historique local | 0,041 s | 2 425,8 points/s | 100 | 196 | OK |
| 500 | SciPy global | 8,960 s | 55,8 points/s | 500 | 996 | OK |
| 500 | Historique local | 0,235 s | 2 123,9 points/s | 500 | 996 | OK |
| 1 000 | Historique local | 0,516 s | 1 939,0 points/s | 1 000 | 1 996 | OK |
| 2 000 | Historique local | 1,251 s | 1 598,7 points/s | 2 000 | 3 996 | OK |
| 5 000 | Historique local | 4,853 s | 1 030,3 points/s | 5 000 | 9 996 | OK |
| 10 000 | Historique local | 16,352 s | 611,6 points/s | 10 000 | 19 996 | contrôle topologique |

Accélérations observées sur les comparaisons terminées :

- 100 points : environ ×10,3 ;
- 500 points : environ ×38,1.

Ces temps dépendent du processeur, de NumPy, de SciPy et de la distribution des points. Ils ne constituent pas une garantie de performance sur une autre machine.

## Validation des grands cas

La vérification géométrique exhaustive de tous les sommets contre toutes les faces coûte `V × F`. Elle est donc désactivée au-delà de 5 000 points par défaut.

Pour les grands cas, la v4 vérifie toujours :

- chaque arête appartient à deux faces ;
- la formule d'Euler `V - E + F = 2` ;
- l'absence de bord libre ;
- la validité de la coloration ;
- la positivité de l'aire et du volume.

La limite peut être modifiée avec :

```bash
python app.py benchmark \
    --reference-max-points 10000 \
    ...
```

Sur une sphère comportant beaucoup de points cosphériques, la référence Qhull peut devenir nettement plus lente que le moteur local. Pour un test de performance pur, utiliser :

```bash
python app.py benchmark \
    --skip-reference-check \
    ...
```

## Chargement DuckDB

La v4 charge les points dans DuckDB à partir d'un DataFrame pandas enregistré comme relation temporaire. Cette insertion en colonnes remplace les insertions Python ligne par ligne et réduit fortement le temps de préparation des grands benchmarks.
