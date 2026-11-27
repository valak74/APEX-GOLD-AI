# Guide — Connecter votre compte DEMO IC Markets (cTrader) à APEX GOLD AI

Ce guide se fait **sur votre ordinateur** (ou votre serveur), pas dans le chat.
**Ne collez jamais** votre Client Secret, vos mots de passe ou vos jetons dans un chat, un email ou un ticket.

## Pré-requis

1. Docker Desktop installé.
2. Le projet cloné : `git clone https://github.com/valak74/APEX-GOLD-AI.git` puis la branche `claude/apex-gold-ai-platform-fk9i6f`.
3. Votre application sur **openapi.ctrader.com** est au statut **Active** (validée par Spotware), avec :
   - **Redirect URI** : `http://localhost:8080/callback` (exactement — sinon utilisez `--redirect-uri` à l'étape 3).
   - Votre **Client ID** et **Client Secret** (page de l'application → *Credentials*).

## 1. Démarrer la plateforme

```bash
cp .env.example .env
python3 -c "import secrets,base64,os;print('APEX_JWT_SECRET='+secrets.token_urlsafe(48));print('APEX_MASTER_KEY='+base64.b64encode(os.urandom(32)).decode())"
# copiez les deux lignes affichées dans .env (remplacez les lignes vides), et changez APEX_DB_PASSWORD
docker compose up -d --build
```

⚠️ **Sauvegardez `APEX_MASTER_KEY`** (gestionnaire de mots de passe). Sans elle, les identifiants broker stockés sont illisibles.

## 2. Créer votre utilisateur admin

```bash
docker compose exec api apex create-user --email vous@exemple.fr --role admin
```

## 3. Lier le compte DEMO cTrader

```bash
docker compose exec api apex ctrader-setup
```

Le programme vous demande :
1. **Client ID** (visible) puis **Client Secret** (masqué pendant la saisie).
2. Il affiche une adresse `https://openapi.ctrader.com/apps/auth?...` → ouvrez-la, connectez-vous avec votre cTrader ID, **autorisez**.
3. Le navigateur part vers `http://localhost:8080/callback?code=...` et affiche une erreur « page introuvable » : **c'est normal**.
   Copiez l'adresse complète de la barre du navigateur et collez-la dans le terminal.
4. Choisissez votre compte DEMO dans la liste (les comptes réels sont refusés dans cette version).

À la fin il affiche : `Identifiant interne : 1` (par exemple).

## 4. Vérifier la connexion

```bash
docker compose exec api apex ctrader-check --account 1
```

Résultat attendu : broker, devise, solde, **la vraie spécification XAUUSD d'IC Markets** (taille de contrat, lot minimum,
commission, distance minimale du stop) et ~20 secondes de prix en direct avec le spread médian.
Le marché de l'or est fermé le week-end : faites ce test en semaine.

👉 **Envoyez-moi la sortie de cette commande** (elle ne contient aucun secret) : elle me permet de vérifier que la
commission et les unités sont interprétées correctement.

## 5. Activer le flux en direct dans la plateforme

Dans `.env` :
```
APEX_MARKET_DATA_SOURCE=ctrader
APEX_CTRADER_BROKER_ACCOUNT_ID=1
```
puis `docker compose up -d api` et ouvrez http://localhost:3000 : le panneau **XAU/USD en direct** affiche bid/ask/spread,
et le calculateur utilise le vrai taux EUR/USD et la vraie commission.

## Ce que fait (et ne fait pas) cette version

- ✅ Lit le compte, la spécification XAUUSD et les prix en direct ; rafraîchit automatiquement le jeton expiré ; se reconnecte seul.
- ✅ Les ordres restent **simulés** (mode PAPER) sur ces prix réels.
- ❌ **N'envoie aucun ordre** au broker, même sur le compte démo (désactivé dans le code).
- ❌ Refuse les comptes réels.

## Dépannage

| Message | Cause probable |
|---|---|
| `CH_CLIENT_AUTH_FAILURE` | Client ID / Secret incorrects, ou application pas encore *Active* |
| `redirect_uri mismatch` / pas de `code` | La Redirect URI ne correspond pas exactement à celle déclarée |
| `CH_ACCESS_TOKEN_INVALID` répété | Relancez `apex ctrader-setup` (nouvelle autorisation) |
| `no tradable XAU/USD symbol` | L'or n'est pas négociable via l'API sur ce compte → prévenez-moi |
| `... not supported yet` | Spécification inhabituelle : la plateforme refuse par sécurité → envoyez-moi le message |
| Aucun prix reçu | Marché fermé (week-end / pause quotidienne ~23h–00h heure de Paris) |

## Importer l'historique (optionnel, recommandé)

```bash
docker compose exec api apex import-dukascopy --from 2025-01-01 --to 2026-11-01
```
Télécharge les ticks Dukascopy heure par heure (reprend là où il s'est arrêté), refuse tout fichier incohérent,
puis calcule les bougies. Plusieurs mois prennent du temps : commencez par un mois pour vérifier.

## Charger le calendrier économique

Créez un fichier `calendrier.json` (format dans `docs/11-phase4-report.md`), puis :
```bash
docker compose cp calendrier.json api:/tmp/calendrier.json
docker compose exec api apex calendar-import /tmp/calendrier.json --source manuel
```
Les nouvelles entrées sont bloquées ±15 min autour de chaque annonce majeure USD (réglable dans les limites de risque).
