# Rapport technique — enveloppe convexe 3D

## 1. Identification de l'exécution

- **Run ID :** `20260622_162611_points_exemple_125e87`
- **Source :** `data/imports/points_exemple.xlsx`
- **Moteur :** `historical`
- **Début UTC :** `2026-06-22T16:26:11.435239+00:00`
- **Fin UTC :** `2026-06-22T16:26:12.113880+00:00`

## 2. Import et qualité des données

- Lignes source : **250**
- Points valides : **250**
- Lignes rejetées : **0**
- Colonne X : `x`
- Colonne Y : `y`
- Colonne Z : `z`
- Colonne identifiant : `point_id`
- Colonne libellé : `label`

Les lignes rejetées sont conservées dans `exports/rejected_points.xlsx`. Les données originales sont conservées dans la table DuckDB `source_points` et les points normalisés dans `clean_points`.

## 3. Algorithme incrémental historique

Le programme cherche d'abord quatre points non coplanaires afin de former un tétraèdre initial. Pour une face orientée `(a,b,c)` et un point candidat `p`, le test géométrique repose sur le produit mixte :

```text
D(a,b,c,p) = (b-a) · ((c-a) × (p-a))
```

Après normalisation de la normale de face, le signe de `n · p + d` indique si la face est visible depuis le nouveau point. Si aucune face n'est visible, le point est intérieur et ne modifie pas l'enveloppe. Sinon :

1. les faces visibles sont supprimées ;
2. les arêtes appartenant à une seule face visible constituent l'horizon ;
3. chaque arête de l'horizon est reliée au nouveau point ;
4. les nouveaux triangles sont orientés vers l'extérieur ;
5. les sommets qui ne participent plus à aucune face sont retirés de la structure active.

La v4 a montré que cette mise à jour locale est très supérieure au recalcul global SciPy/Qhull à chaque insertion. Sur notre poste de test :

| Points sur une sphère | SciPy global | Historique local | Accélération |
|---:|---:|---:|---:|
| 1 000 | 45,918 s | 0,774 s | ×59,34 |
| 1 500 | 102,594 s | 1,354 s | ×75,77 |
| 2 000 | 181,721 s | 2,077 s | ×87,49 |

## 4. Aire, volume et topologie

L'aire est la somme des aires triangulaires :

```text
A_face = 1/2 × ||(b-a) × (c-a)||
```

Le volume est obtenu par la somme orientée des tétraèdres formés avec l'origine :

```text
V = | Σ a · (b × c) / 6 |
```

La cohérence topologique est contrôlée par :

```text
V_sommets - E_arêtes + F_faces = 2
```

Pour une surface triangulée fermée, la relation `F = 2V - 4` est également vérifiée. Chaque arête doit appartenir exactement à deux faces.

## 5. Coloration

Chaque triangle possède au plus trois voisins, un par arête. Une coloration gloutonne choisit donc une couleur disponible parmi rouge, jaune, vert et bleu. Le contrôle final garantit que deux faces voisines n'ont jamais la même couleur.

## 6. Résultats

- Points traités : **250**
- Sommets extrêmes : **63**
- Faces triangulaires : **122**
- Arêtes : **183**
- Aire : **10.7984118603**
- Volume : **3.1957535462**
- Temps géométrique : **0.087132 s**
- Temps total : **0.678642 s**
- Débit : **2869.20 points/s**
- Mémoire maximale observée : **1010.72 Mo**

## 7. Validations automatiques

| Test | Résultat | Valeur | Tolérance ou attendu | Détails |
|---|---|---|---|---|
| invariants_topologiques_et_geometriques | OK | OK | convexité complète si V <= 5000 | Arêtes appariées, Euler, coloration, aire et volume validés. Convexité complète validée. |
| formule_euler | OK | 2 | V - E + F = 2 | V=63, E=183, F=122 |
| relation_polyedre_triangule | OK | F=122 | 2V-4=122 | Relation déduite d'Euler lorsque toutes les faces sont triangulaires. |
| coloration_quatre_couleurs | OK | 0,1,2,3 | couleurs dans {0,1,2,3} | Deux triangles voisins ne doivent jamais partager la même couleur. |
| reference_scipy_volume | OK | 8.881784e-16 | <= 3.195754e-08 | Volume SciPy=3.1957535462; historique=3.1957535462 |
| reference_scipy_aire | OK | 8.881784e-15 | <= 1.079841e-07 | Aire SciPy=10.7984118603; historique=10.7984118603 |
| reference_scipy_faces | OK | 122 | 122 | Comparaison du nombre de triangles finaux. |

## 8. Base DuckDB de l'exécution

La base `points.duckdb` de ce répertoire contient :

- `source_points` : copie tabulaire de la source ;
- `clean_points` : coordonnées normalisées ;
- `rejected_points` : lignes rejetées et motifs ;
- `hull_vertices` : sommets extrêmes finaux ;
- `hull_faces` : triangles et couleurs ;
- `hull_steps` : points de contrôle du calcul ;
- `validation_results` : résultats des tests ;
- `run_info` : métriques globales.

Les scripts d'attachement et d'exploration sont dans le sous-répertoire `sql`.

## 9. Montée en charge

Excel convient aux démonstrations et aux volumes modérés, mais son format est limité à 1 048 576 lignes par feuille et son décodage est relativement coûteux. Pour les grands corpus, la même commande accepte Parquet et CSV. La stratégie recommandée est :

1. stocker les coordonnées en Parquet ;
2. filtrer et typer les colonnes avec DuckDB ou **Polars** ;
3. lire les points par lots ou en mode streaming ;
4. conserver le moteur géométrique NumPy ;
5. n'afficher que la surface finale ou des étapes échantillonnées.

Polars est une piste pertinente pour la préparation de très grandes tables grâce à son exécution paresseuse et à son moteur en colonnes. Il ne remplace pas le moteur géométrique : il optimise l'ingestion, le nettoyage et les transformations avant l'algorithme d'enveloppe.

## 10. Fichiers produits

- `points.duckdb`
- `exports/clean_points.xlsx`
- `exports/rejected_points.xlsx`
- `exports/final_vertices.xlsx`
- `exports/final_faces.xlsx`
- `exports/results.xlsx`
- `exports/final_vertices.csv`
- `exports/final_faces.csv`
- `exports/final_hull.vtp`
- `exports/final_hull.ply`
- `exports/final_hull.png`
- `metrics.json`
- `sql/attach_database.sql`
- `sql/inspect_database.sql`
- `sql/detach_database.sql`
