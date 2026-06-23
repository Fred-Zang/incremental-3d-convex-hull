# Feuille de route

## Court terme

- compléter les benchmarks à 5 000, 10 000, 25 000 et 50 000 points ;
- mesurer la mémoire maximale et séparer les temps import/calcul/export/rendu ;
- ajouter un mode de visualisation échantillonné pour les très grands nuages ;
- enrichir le rapport automatique avec des graphiques de performance.

## Grands volumes

- lecture paresseuse de Parquet avec Polars `scan_parquet` ;
- lecture directe et filtrage par DuckDB ;
- alimentation du moteur par lots ;
- conservation en mémoire des seules colonnes utiles et de la structure active ;
- évaluation de stratégies de partitionnement spatial.

## Robustesse géométrique

- prédicats d’orientation plus robustes pour les points presque coplanaires ;
- normalisation automatique des coordonnées de très grande amplitude ;
- politique explicite pour les doublons et points situés sur une face ;
- tests de propriétés sur des nuages générés aléatoirement.

## Applications scientifiques

- import PDB/mmCIF pour des coordonnées atomiques ;
- import LAS/LAZ/COPC pour des nuages LiDAR ;
- comparaison avec formes alpha ou enveloppes concaves ;
- calculs complémentaires de compacité, diamètre et axes principaux.
