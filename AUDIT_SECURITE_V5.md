# Audit de sécurité — incremental-3d-convex-hull v5.0.0

**Date :** 23 juin 2026  
**Périmètre :** archive `incremental-3d-convex-hull-v5-github-ready.zip` correspondant au dépôt v5.0.0 publié.  
**Type d'application :** outil scientifique local en ligne de commande, sans serveur réseau, important des fichiers Excel/CSV/Parquet, créant une base DuckDB, des exports tabulaires et un rendu PyVista.

## 1. Conclusion exécutive

Aucune vulnérabilité critique ou élevée n'a été identifiée dans le code applicatif. Le projet ne contient ni `eval`, ni `exec`, ni désérialisation `pickle`, ni appel de shell ou de sous-processus, ni secret intégré. La majorité des requêtes DuckDB utilisent des paramètres.

Le niveau de risque est :

- **faible** lorsque les fichiers d'entrée sont créés par l'utilisateur ou proviennent d'une source de confiance ;
- **modéré** lorsque des fichiers Excel, CSV ou Parquet tiers et non fiables sont traités puis que les exports sont ouverts dans Excel ou LibreOffice.

Les principaux correctifs recommandés pour une v5.0.1 sont :

1. neutraliser les formules dans les exports Excel/CSV ;
2. ajouter `defusedxml` pour la lecture Excel ;
3. ajouter des limites de taille, de lignes et de colonnes avant le chargement complet ;
4. verrouiller les dépendances et les auditer automatiquement ;
5. durcir les chemins SQL générés et les permissions locales.

## 2. Méthode et outils

### Contrôles exécutés

| Contrôle | Résultat |
|---|---:|
| Tests Pytest | **15 réussis** |
| Ruff | **aucun problème** |
| Bandit 1.9.4 | **1 signalement moyen**, non exploitable actuellement mais à durcir |
| detect-secrets 1.5.0 | **aucun secret détecté** |
| Recherche manuelle des primitives dangereuses | aucun `eval`, `exec`, `pickle`, `subprocess`, `os.system` ou `shell=True` |
| Test d'injection de formule | vulnérabilité reproduite depuis un CSV contrôlé |
| pip-audit | tentative effectuée, mais service de vulnérabilités inaccessible depuis l'environnement d'audit |

### Limites

- Le dépôt ne fournit pas de fichier de verrouillage complet avec versions exactes et hashes. Un audit CVE reproductible de l'environnement installé n'est donc pas possible à partir du seul dépôt.
- Les paramètres de sécurité du dépôt GitHub distant, la protection de branche, les règles de fusion et les extensions VS Code ne font pas partie de l'audit du code.
- L'audit est statique et dynamique sur les chemins principaux ; il ne constitue pas une preuve formelle d'absence de vulnérabilité.

## 3. Points positifs

- Application locale, sans port réseau ouvert et sans système d'authentification à protéger.
- Requêtes SQL de données paramétrées avec `?` dans la majorité du code.
- Aucune exécution de commande système à partir d'une donnée utilisateur.
- Aucune désérialisation Python dangereuse.
- Aucune clé, aucun jeton ou mot de passe détecté.
- Les bases DuckDB et les répertoires `data/runs` sont exclus de Git.
- Le workflow GitHub Actions déclare `permissions: contents: read`.
- Les sorties ont des noms fixes à l'intérieur d'un répertoire de run unique.
- Les coordonnées sont converties en nombres et les valeurs non finies sont rejetées.

## 4. Constats détaillés

### AS-01 — Injection de formules dans les exports Excel et CSV

**Sévérité : moyenne**  
**CWE : CWE-1236 — Improper Neutralization of Formula Elements in a CSV File**

**Emplacements :**

- `pipeline_v5.py:667-680`
- `pipeline_v5.py:1006-1007`

Les libellés proviennent du fichier source et sont écrits sans neutralisation dans :

- `clean_points.xlsx` ;
- `final_vertices.xlsx` ;
- `results.xlsx` ;
- `final_vertices.csv`.

Un CSV contenant un libellé comme :

```text
=HYPERLINK("https://example.invalid","click")
```

produit une cellule Excel de type **formule** dans `final_vertices.xlsx`. Les préfixes `+`, `-`, `@`, tabulation et retour chariot sont également interprétés comme dangereux par plusieurs tableurs lors de l'ouverture d'un CSV.

**Impact :** ouverture d'un export contrôlé par un tiers pouvant déclencher des formules, des liens ou des fonctionnalités historiques du tableur, selon la configuration et l'interaction de l'utilisateur.

**Correctif recommandé :**

- créer une fonction centrale de neutralisation des chaînes exportées ;
- préfixer les valeurs commençant par `=`, `+`, `-`, `@`, tabulation, CR ou LF avec une apostrophe ;
- appliquer cette fonction à chaque colonne texte avant les exports Excel et CSV ;
- ajouter un test automatisé avec un libellé malveillant.

### AS-02 — Protection XML absente pour les fichiers Excel non fiables

**Sévérité : moyenne**

**Emplacements :**

- `pipeline_v5.py:138-155`
- `requirements.txt`

`pandas.read_excel(..., engine="openpyxl")` est utilisé, mais `defusedxml` n'est pas déclaré dans les dépendances du projet. La documentation openpyxl recommande explicitement ce paquet pour se protéger contre les attaques XML de type expansion exponentielle ou « billion laughs ».

**Impact :** déni de service par consommation excessive de mémoire ou de CPU lors de l'ouverture d'un classeur spécialement construit.

**Correctif recommandé :**

```text
defusedxml>=0.7.1,<0.8
```

Ajouter également un test de présence au démarrage lors de la lecture d'un fichier Excel tiers.

### AS-03 — Absence de limites d'ingestion et risque de déni de service

**Sévérité : moyenne**

**Emplacements :**

- `pipeline_v5.py:138-155`
- `pipeline_v5.py:918-976`

Le fichier entier est chargé dans un DataFrame avant l'application de `--max-seconds`. Il n'existe pas de limite sur :

- la taille du fichier ;
- le nombre de lignes ;
- le nombre de colonnes ;
- la taille décompressée d'un classeur ;
- la mémoire maximale ;
- la taille des chaînes ;
- l'amplitude des coordonnées.

Le pipeline duplique ensuite les données dans la copie source, DuckDB et plusieurs exports Excel/CSV.

**Impact :** saturation de la mémoire, du disque ou du CPU par un fichier très volumineux, compressé de manière hostile ou géométriquement défavorable.

**Correctif recommandé :**

- options `--max-input-mb`, `--max-rows`, `--max-columns`, `--max-label-length` ;
- contrôle de la taille avant lecture ;
- lecture CSV par morceaux et Parquet via métadonnées/streaming ;
- refus ou avertissement renforcé pour Excel au-delà d'un seuil ;
- contrôle de l'espace disque disponible ;
- recentrage et mise à l'échelle des coordonnées extrêmes ;
- pour les corpus non fiables, exécution dans un conteneur avec limites mémoire/CPU.

### AS-04 — Dépendances non verrouillées et audit CVE non reproductible

**Sévérité : moyenne**

**Emplacements :**

- `requirements.txt`
- `requirements-dev.txt`
- `.github/workflows/ci.yml`

Les dépendances utilisent des intervalles larges, par exemple `numpy>=2.0,<3`. Deux installations réalisées à des dates différentes peuvent donc obtenir des arbres différents. `pytest` est aussi présent dans les dépendances d'exécution alors qu'il devrait rester dans le groupe de développement.

**Impact :** dérive fonctionnelle, introduction future d'une version vulnérable ou compromise, impossibilité de reproduire exactement l'audit.

**Correctif recommandé :**

- séparer les dépendances d'exécution et de développement ;
- générer un lockfile avec versions exactes et hashes SHA-256 ;
- installer en CI avec `--require-hashes` ;
- ajouter `pip-audit` à la CI ;
- ajouter Dependabot pour `pip` et `github-actions` ;
- produire éventuellement un SBOM CycloneDX pour chaque release.

### AS-05 — Actions GitHub référencées par tags mutables

**Sévérité : faible à moyenne**

**Emplacement :** `.github/workflows/ci.yml`

Le workflow utilise :

```text
actions/checkout@v6
actions/setup-python@v6
```

Ces actions sont officielles et le workflow dispose seulement de `contents: read`, ce qui réduit fortement le risque. Toutefois, les tags majeurs restent mutables.

**Correctif recommandé :** épingler chaque action sur le SHA complet du commit vérifié, avec le tag conservé en commentaire, puis laisser Dependabot proposer les mises à jour.

### AS-06 — Chemin non échappé dans le script SQL généré

**Sévérité : faible**

**Emplacement :** `pipeline_v5.py:683-692`

Le chemin de la base est inséré directement entre apostrophes dans `attach_database.sql`. Une valeur `--runs-dir` contenant une apostrophe peut produire un script invalide ou injecter une instruction DuckDB lorsque l'utilisateur exécute manuellement ce fichier.

**Correctif recommandé :**

- résoudre le chemin absolu ;
- remplacer chaque apostrophe `'` par `''` avant génération ;
- ajouter un test avec un chemin contenant une apostrophe.

### AS-07 — Construction dynamique des noms de tables

**Sévérité : faible**

**Emplacement :** `pipeline_v5.py:309-321`

Bandit signale les requêtes `DROP TABLE` et `CREATE TABLE` construites avec une f-string. Les appels actuels passent uniquement des constantes internes, donc aucune exploitation directe n'a été trouvée.

**Correctif recommandé :** ajouter une liste blanche immuable des noms autorisés et refuser toute autre valeur. Cela transforme le signalement Bandit en invariant explicite et protège une évolution future.

### AS-08 — Duplication de données potentiellement sensibles

**Sévérité : faible à moyenne selon les données**

**Emplacements :**

- `pipeline_v5.py:930-940`
- `pipeline_v5.py:1001-1048`

Chaque traitement conserve :

- une copie complète du fichier original ;
- la table `source_points` ;
- les points nettoyés ;
- plusieurs exports ;
- le chemin source dans les métriques et le rapport.

Le `.gitignore` évite une publication accidentelle des runs, mais les fichiers locaux ne sont pas chiffrés et héritent généralement des permissions de l'utilisateur.

**Correctif recommandé :**

- documenter explicitement cette duplication ;
- option `--no-copy-source` ;
- permissions `0700` pour le run et `0600` pour les fichiers sensibles ;
- option de suppression automatique après une durée donnée ;
- éviter les chemins absolus ou identifiants sensibles dans les rapports partageables.

### AS-09 — Fichiers `.xlsm` acceptés et copie des macros

**Sévérité : faible**

**Emplacements :**

- `pipeline_v5.py:145-149`
- `pipeline_v5.py:930-932`

Le pipeline n'exécute pas les macros. Cependant, un fichier `.xlsm` est accepté puis copié dans le répertoire de run. Une macro malveillante reste donc présente dans `source_original.xlsm` et pourrait être exécutée si le fichier est rouvert dans Excel avec les macros activées.

**Correctif recommandé :** refuser `.xlsm` par défaut ou exiger une option `--allow-macro-workbook` accompagnée d'un avertissement clair. Ne jamais présenter la copie comme « nettoyée ».

### AS-10 — Suivi des liens symboliques lors de la copie source

**Sévérité : faible**

**Emplacement :** `pipeline_v5.py:918-932`

`shutil.copy2()` suit par défaut le lien symbolique fourni en entrée. Dans le contexte actuel, le chemin est explicitement choisi par l'utilisateur, ce qui réduit le risque.

**Correctif recommandé :** résoudre le chemin, vérifier qu'il s'agit d'un fichier régulier et refuser les liens symboliques pour les traitements de fichiers tiers.

### AS-11 — Injection de contenu Markdown ou de caractères de contrôle

**Sévérité : faible**

**Emplacements :**

- `pipeline_v5.py:695-846`
- messages terminaux utilisant le nom de fichier

Le chemin source et certains noms de colonnes sont intégrés sans échappement dans `report.md`. Un nom spécialement construit peut altérer la mise en forme du rapport ou injecter un lien/HTML Markdown. Des caractères ANSI dans un nom de fichier peuvent aussi perturber le terminal.

**Correctif recommandé :** supprimer les caractères de contrôle et échapper les caractères Markdown avant génération du rapport et affichage dans le terminal.

## 5. Ordre de correction proposé pour v5.0.1

### Priorité 1 — avant d'annoncer la prise en charge de fichiers tiers non fiables

1. neutralisation des formules Excel/CSV ;
2. ajout de `defusedxml` ;
3. limites de taille/lignes/colonnes ;
4. refus ou avertissement `.xlsm` ;
5. tests de sécurité correspondants.

### Priorité 2 — chaîne de publication

1. lockfile et hashes ;
2. `pip-audit` dans GitHub Actions ;
3. Dependabot ;
4. actions GitHub épinglées par SHA ;
5. `SECURITY.md` avec procédure de signalement.

### Priorité 3 — durcissement local

1. permissions 0700/0600 ;
2. option `--no-copy-source` ;
3. échappement du chemin SQL ;
4. liste blanche des tables ;
5. nettoyage Markdown/terminal ;
6. rejet des liens symboliques.

## 6. Verdict de publication

La v5.0.0 est adaptée à une démonstration GitHub et au traitement de données de confiance. Elle ne devrait pas encore être décrite comme sûre pour des fichiers tiers hostiles.

Formulation recommandée dans le README actuel :

> Le pipeline est un outil scientifique local. Jusqu'à la version de durcissement v5.0.1, traiter uniquement des fichiers provenant d'une source de confiance. Les fichiers d'entrée peuvent être copiés dans le répertoire d'exécution et les exports tabulaires doivent être ouverts avec les précautions habituelles.

Après correction des priorités 1 et 2, le niveau de risque résiduel deviendra faible pour l'usage local prévu.
