# SQL et bases DuckDB v5

La commande `app.py process` crée une base distincte dans chaque répertoire `data/runs/...`.

Les trois scripts à exécuter dans l'extension DuckDB de Visual Studio Code sont générés directement dans le sous-répertoire `sql` du run :

```text
attach_database.sql
inspect_database.sql
detach_database.sql
```

Il n'est plus nécessaire de détacher une ancienne base avant de lancer un nouveau traitement, puisque chaque exécution écrit dans son propre fichier.
