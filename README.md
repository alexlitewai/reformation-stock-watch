# Robot de surveillance — Reformation

Surveille la dispo de tailles precises sur un produit thereformation.com et envoie
un push sur le telephone (via ntfy.sh) des qu'une taille surveillee revient en stock.

Surveillance actuelle : **Balia Linen Dress**, couleur Slate Check (STC), **tailles 2 et 4**.
Verification **toutes les ~100 secondes, 24/7**, via GitHub Actions.

GitHub n'honore qu'environ 13 % d'un cron `*/5` (ecart reel mesure : ~40 min).
Le contournement : chaque run verifie en continu pendant ~38 min, ce qui couvre
l'ecart jusqu'au declenchement suivant.

## Recevoir les notifs

1. Installer l'app **ntfy** (App Store / Google Play)
2. S'abonner au topic secret (il n'est volontairement ecrit nulle part dans ce depot :
   il vit dans le secret GitHub `NTFY_TOPIC` et dans le gestionnaire de mots de passe).

Le topic fait office de mot de passe : quiconque le connait peut lire les notifs.
Ne jamais le committer. Pour en changer :

    gh secret set NTFY_TOPIC

## Commandes

    python3 watch.py --status       # etat de toutes les tailles, maintenant
    python3 watch.py --test-notif   # envoie une notif de test
    python3 watch.py --notify-now   # notifie l'etat courant meme sans changement
    python3 watch.py                # une verification (ce que lance launchd)

    tail -f watch.log               # journal

## GitHub Actions (execution 24/7)

Le workflow `.github/workflows/stock-watch.yml` fait tourner le robot sur les runners
GitHub, donc **meme Mac eteint**. Le topic ntfy n'est PAS dans le depot : il vient du
secret `NTFY_TOPIC`. L'etat (quelles tailles ont deja alerte) survit entre les runs via
le cache Actions, sans polluer l'historique de commits.

    gh workflow run stock-watch                      # verification immediate
    gh workflow run stock-watch -f notify_now=true   # test de notif de bout en bout
    gh run list --workflow=stock-watch   # historique
    gh run view --log                    # journal du dernier run

`keepalive.yml` fait un commit par mois : GitHub desactive les crons apres 60 jours
sans activite sur le depot.

Depot : https://github.com/alexlitewai/reformation-stock-watch

## Execution locale (desactivee, en secours)

Le LaunchAgent macOS faisait la meme chose toutes les 10 min quand le Mac est allume.
Il est desactive pour eviter les notifs en double (plist renomme en `.disabled` dans
`~/Library/LaunchAgents/`). Pour le reactiver, remettre l'extension `.plist` puis
`launchctl bootstrap`.
En local le topic doit venir de l'environnement :

    NTFY_TOPIC=<topic> python3 watch.py --status

    launchctl bootout   gui/$(id -u)/com.dianealex.reformation-watch   # arreter
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.dianealex.reformation-watch.plist

## Configuration (`config.json`)

- `watch.url` / `watch.color` / `watch.sizes` : produit, code couleur, tailles surveillees
  (les libelles sont ceux du site : "2", "4", ou "XS", "S"... selon le produit)
- `repeat_hours` : rappel si l'article reste en stock (defaut 12 h)

Pour surveiller un autre article : remplacer `url`, `color` (code du swatch, ex. `WHT`,
`BLK`, `PMN`, `RAW`) et `sizes`, puis supprimer `state.json`.

## Fonctionnement

La page produit contient un bloc JSON-LD listant chaque SKU (`1314884STC002`) avec son
`availability`. Les libelles de taille viennent des boutons du selecteur. Une alerte
part uniquement sur la **transition** rupture -> en stock (pas de spam), avec un rappel
au bout de `repeat_hours` si l'article est toujours dispo. Apres ~1 h d'echecs consecutifs
(reseau, page modifiee), une notif d'avertissement est envoyee.

## Limites

- Les crons GitHub sont "best effort" : un run peut etre retarde de plusieurs minutes
  quand la plateforme est chargee. La granularite ~100 s est une cible, pas une garantie.
- Si Reformation change la structure de sa page, le parsing casse -> notif d'avertissement.
- Le robot notifie seulement : il n'achete rien et ne met rien au panier.
