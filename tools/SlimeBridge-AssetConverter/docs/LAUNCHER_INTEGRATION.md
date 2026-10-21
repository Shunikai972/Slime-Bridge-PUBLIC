# Intégration au launcher SlimeBridge

Trois interfaces équivalentes (`API_VERSION = 1`) :

## 1. API Python (`sbconv.api`)

```python
from sbconv import api
api.scan(sr1_path=None, sr2_path=None)                       # {'ok', 'games': {'SR1': {...}, 'SR2': {...}}}
api.list_species(output, sr1_path, sr2_path, game='both', kind='all')
api.convert(output, direction='both', slimes=('Ringtail',), all_species=False, kind='all',
            sr1_path=..., sr2_path=..., dry_run=False, force=False, log=print)
api.verify(output, direction='both', slimes=(), all_species=True, sr1_path=..., sr2_path=...)
api.status(output)
api.engines()
```

Toutes renvoient un dict JSON-sérialisable avec `ok`. Les échecs par espèce sont dans `results[*]` (`ok`, `status`,
`error`) : une espèce en échec n'arrête pas le lot.

## 2. CLI avec `--json`

`sbconv convert ... --json` imprime le même dict sur stdout (journal sur stderr). Code retour 0 = tout `ok`.

## 3. Protocole JSON-lines : `sbconv serve --output DIR`

Une requête par ligne sur stdin : `{"id":"1","op":"convert","args":{"direction":"sr2-to-sr1","slimes":["Ringtail"],"sr1_path":"...","sr2_path":"..."}}`.
Réponses sur stdout : `{"event":"hello",...}` au démarrage, puis par requête 0..n `{"id","event":"log","message"}` et exactement un
`{"id","event":"result","ok":bool,"result":{...}}` (ou `{"event":"error","error":...}`). Opérations : `scan, list, convert, verify, status, engines`.

## Brancher le paquet runtime dans SlimeBridge

* Les noms canoniques `slime.<slug>` / `largo.<a>.<b>` sont ceux du dépôt SlimeRancher-Bridge.
* Le chargeur du dépôt (`ImportedModelLoader`) consomme un cache `model.json` à tableaux JSON ; le paquet runtime est plus
  riche (LOD, matériaux en recettes, animations, adaptateurs). Un exportateur `runtime → bridge-cache` est la prochaine
  brique naturelle (non fournie : le format cible du chargeur doit d'abord être figé côté launcher).
* Le launcher doit rejouer `sbconv convert` quand l'empreinte d'une installation change (le `params_key` le détecte
  automatiquement) et ne jamais distribuer `output/`.
