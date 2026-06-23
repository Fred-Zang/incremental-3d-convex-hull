# Mémo technique v5 — reconstruction d'une enveloppe convexe 3D incrémentale

## 1. Finalité

Ce projet reconstitue en Python un programme de géométrie algorithmique initialement développé en Super-Pascal en 1991. Il traite un flux de points tridimensionnels, ne conserve dans sa structure active que les sommets extrêmes et construit progressivement une surface convexe triangulée.

La v5 ajoute une chaîne scientifique reproductible : import d'un fichier réel, contrôle qualité, stockage DuckDB, calcul, validations, exports et visualisation.

## 2. Périmètre fonctionnel

La commande principale accepte Excel, CSV ou Parquet. Elle :

1. lit la table avec pandas ;
2. détecte ou reçoit les colonnes X, Y et Z ;
3. valide les valeurs et les identifiants ;
4. crée une base DuckDB propre à l'exécution ;
5. construit l'enveloppe avec le moteur historique ;
6. enregistre des points de contrôle ;
7. vérifie les invariants topologiques ;
8. compare avec SciPy lorsque la taille le permet ;
9. exporte les résultats ;
10. ouvre un rendu PyVista interactif sur demande.

## 3. Modèle géométrique

### 3.1 Enveloppe convexe

Pour un ensemble de points `P`, l'enveloppe convexe `conv(P)` est le plus petit ensemble convexe contenant tous les points. En dimension 3, elle est représentée par un polyèdre fermé dont les faces sont triangulées.

### 3.2 Tétraèdre initial

Le moteur cherche successivement :

- deux points distincts ;
- un troisième point non aligné ;
- un quatrième point non coplanaire.

La non-coplanarité est contrôlée par le déterminant :

```text
det([b-a, c-a, d-a]) ≠ 0
```

### 3.3 Visibilité d'une face

Pour une face orientée `(a,b,c)`, la normale est :

```text
n = (b-a) × (c-a)
```

Le point `p` voit la face lorsque le produit mixte est positif au-delà de la tolérance numérique :

```text
D = n · (p-a) > ε
```

### 3.4 Horizon

Les faces visibles forment la portion de la surface qui doit disparaître. Une arête appartenant à une seule face visible sépare la zone visible de la zone conservée : elle appartient à l'horizon.

Chaque arête de l'horizon est reliée au nouveau point. Les triangles ainsi créés constituent le nouveau capuchon.

### 3.5 Compaction

Après la suppression locale, un ancien sommet peut ne plus appartenir à aucune face. Il est alors retiré de la structure active. Les points intérieurs restent néanmoins présents dans DuckDB pour l'audit.

## 4. Grandeurs calculées

### 4.1 Aire

Pour un triangle `(a,b,c)` :

```text
A = 1/2 ||(b-a) × (c-a)||
```

L'aire totale est la somme des aires des faces.

### 4.2 Volume

Une face orientée définit avec l'origine un tétraèdre signé :

```text
V_face = a · (b × c) / 6
```

Le volume final est la valeur absolue de la somme des contributions.

## 5. Validations

### 5.1 Surface fermée

Chaque arête doit appartenir exactement à deux triangles.

### 5.2 Formule d'Euler

Pour un polyèdre convexe fermé :

```text
V - E + F = 2
```

### 5.3 Relation triangulée

Comme chaque face possède trois arêtes et que chaque arête est partagée par deux faces :

```text
3F = 2E
```

Combinée à Euler :

```text
F = 2V - 4
E = 3V - 6
```

### 5.4 Convexité

Pour chaque plan de face orienté vers l'extérieur, tous les sommets doivent être sur le plan ou du côté intérieur. Le contrôle complet est exécuté sous un seuil configurable afin d'éviter une matrice trop volumineuse.

### 5.5 Référence SciPy

Sous un second seuil configurable, SciPy/Qhull calcule une enveloppe finale indépendante sur tous les points. Les critères comparés sont l'aire, le volume et le nombre de faces.

### 5.6 Coloration

Le graphe dual associe un sommet à chaque face et une arête à chaque paire de faces voisines. Chaque triangle ayant au plus trois voisins, une coloration gloutonne dispose toujours d'une quatrième couleur au besoin. Les couleurs servent à la lisibilité et à un contrôle combinatoire, pas au test géométrique de convexité.

## 6. Architecture des données

Chaque exécution crée une base indépendante comportant :

| Table | Rôle |
|---|---|
| `source_points` | copie de toutes les colonnes de la source |
| `clean_points` | points numériques normalisés |
| `rejected_points` | lignes invalides et motifs |
| `hull_vertices` | sommets extrêmes finaux |
| `hull_faces` | triangles et couleurs |
| `hull_steps` | métriques intermédiaires échantillonnées |
| `validation_results` | résultats des contrôles |
| `run_info` | synthèse de l'exécution |

La base par run supprime la nécessité de détacher manuellement la base d'une précédente exécution avant un nouveau calcul.

## 7. Performances validées

Résultats observés sur notre station Linux pour le cas difficile où tous les points sont placés sur une sphère :

| N | SciPy global par insertion | Historique local | Débit historique | Accélération |
|---:|---:|---:|---:|---:|
| 1 000 | 45,918 s | 0,774 s | 1 292,2 points/s | ×59,34 |
| 1 500 | 102,594 s | 1,354 s | 1 107,8 points/s | ×75,77 |
| 2 000 | 181,721 s | 2,077 s | 963,0 points/s | ×87,49 |

Les validations SciPy ont toutes réussi. Les écarts sur l'aire et le volume étaient compris entre zéro et quelques `10⁻¹⁴`.

Le gain augmente avec N, car le prototype SciPy reconstruit toute l'enveloppe après chaque point, tandis que le moteur historique ne modifie que la zone visible.

## 8. Excel, pandas, DuckDB, Parquet et Polars

### 8.1 Excel

Excel est adapté à la démonstration, au contrôle manuel et aux jeux modérés. Une feuille est limitée à 1 048 576 lignes. La décompression XML et l'interprétation des cellules augmentent aussi le temps d'import.

### 8.2 pandas

pandas apporte une interface simple pour lire un fichier réel, convertir les types, produire les fichiers de rejet et écrire les résultats Excel. Dans la v5, toute la table est matérialisée en mémoire.

### 8.3 DuckDB

DuckDB apporte un stockage local sans serveur, des requêtes SQL, une traçabilité complète et une bonne intégration avec les tables en colonnes.

### 8.4 Parquet

Parquet doit devenir le format privilégié au-delà des volumes raisonnables pour Excel. Il conserve les types, compresse les données et permet la lecture sélective des colonnes.

### 8.5 Polars

Le nom correct de la bibliothèque est **Polars**. Une évolution destinée aux millions de lignes pourra utiliser :

- `scan_parquet` pour une lecture paresseuse ;
- la sélection limitée à l'identifiant et aux trois coordonnées ;
- le filtrage des valeurs invalides avant matérialisation ;
- le traitement par lots ;
- la conversion vers NumPy seulement au moment nécessaire.

Polars optimisera la partie tabulaire. Il ne remplace pas l'algorithme géométrique et ne résout pas à lui seul le cas où presque tous les points restent extrêmes.

## 9. Limites actuelles

- Le fichier source est encore chargé entièrement dans pandas.
- La liste des `PointRecord` est matérialisée en mémoire.
- La convexité exhaustive est quadratique en nombre de sommets et donc limitée par un seuil.
- Le rendu de dizaines de milliers de triangles reste possible, mais l'affichage de tous les points source n'est pas souhaitable.
- Les ensembles presque coplanaires exigent une tolérance numérique soigneusement choisie.
- Le moteur suppose une enveloppe triangulée et des coordonnées en virgule flottante double précision.

## 10. Évolutions prévues

1. lecteur Parquet en streaming avec Polars ou DuckDB ;
2. alimentation du moteur par lots sans liste Python globale ;
3. stratégie de prédicat géométrique robuste pour les cas presque dégénérés ;
4. journal de performance mémoire plus fin ;
5. benchmarks 5 000, 10 000, 25 000, 50 000 points et au-delà ;
6. démonstration finale sur un grand nuage aléatoire ;
7. import de coordonnées atomiques PDB ou mmCIF ;
8. comparaison avec formes alpha pour représenter les concavités.

## 11. Reproductibilité

Le générateur accepte une graine. Le fichier produit est ensuite relu par la même commande que les données réelles, ce qui évite un chemin de calcul spécial réservé aux tests.

Chaque run conserve : la source, la base, les exports, le rapport, les validations et les scripts SQL. Cette structure permet de reproduire, auditer et comparer les résultats.
