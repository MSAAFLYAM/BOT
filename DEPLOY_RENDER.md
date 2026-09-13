# 🚀 Déploiement sur Render (GRATUIT, sans carte bancaire)

Render est la **seule plateforme gratuite sans carte bancaire** qui permet au bot de
fonctionner correctement :
- ✅ **Aucune carte bancaire** (GitHub sign-in)
- ✅ **Pas d'allowlist** — scraping Amazon, Google, Groq, Blogger illimité
- ✅ **750 h/mois gratuites** (suffisant pour un service web)
- ❌ Web services free *spin down* après 15 min sans trafic → le workflow
  `keep-alive.yml` ping toutes les 10 min pour le garder éveillé
- ❌ Filesystem éphémère → la base SQLite et le cache sont perdus au redéploiement
  (les articles Blogger sont sauvegardés côté Blogger, c'est l'essentiel)

---

## Étape 1 — Créer le service (5 minutes)

1. Rendez-vous sur **https://dashboard.render.com/register**
2. Cliquez **"Sign up with GitHub"** — **aucune carte demandée**
3. Une fois connecté, cliquez **"New +"** → **"Blueprint"**
4. Choisissez le dépôt **`MSAAFLYAM/BOT`**
5. Render détecte automatiquement le fichier **`render.yaml`** (déjà préparé)
6. Cliquez **"Apply"** → le déploiement démarre (~5 min)

> ⚠️ Si "Blueprint" ne détecte pas le fichier, utilisez **"New +" → "Web Service"**,
> connectez le repo, et renseignez :
> - **Runtime** : `Python 3`
> - **Build Command** : `pip install -r requirements.txt`
> - **Start Command** : `gunicorn main:flask_app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
> - **Instance Type** : `Free`

## Étape 2 — Remplir les variables secrètes

Dans le dashboard du service → onglet **"Environment"**, le `render.yaml` a créé les
variables *vides* (synced à false). Remplissez-les avec les valeurs de votre `.env`
(elles NE doivent PAS être commitées) :

| Variable | Valeur (copier depuis `.env`) |
|----------|-------------------------------|
| `BOT_TOKEN` | votre token Telegram |
| `WEBHOOK_SECRET` | votre valeur (`.env`) |
| `TRIGGER_SECRET` | `JzTSNMdEaG4FrY7KoUeX0gHWvc2Vb9mp` |
| `GROQ_API_KEY` | votre clé |
| `BLOGGER_CLIENT_ID` / `_SECRET` / `_REFRESH_TOKEN` / `BLOG_ID` | vos valeurs |

Les autres (CHANNEL_ID, ADMIN_CHAT_ID, AFFILIATE_TAG…) sont déjà pré-remplis dans le blueprint.

> ⚠️ **Ne PAS** définir `PUBLIC_DOMAIN` avec un nom différent de celui du service.
> Render donne à votre app l'URL `https://amazon-affiliate-bot.onrender.com`.
> Le `PUBLIC_DOMAIN=amazon-affiliate-bot.onrender.com` du render.yaml doit **matcher
> l'URL réelle** (sans `https://`). Si Render vous attribue un autre sous-domaine,
> corrigez cette variable.

Puis **"Save & Deploy"**.

## Étape 3 — Configurer le webhook Telegram

Au démarrage, `main.py` (`_start_services()` ligne 2580) détecte `PUBLIC_DOMAIN` et
enregistre automatiquement le webhook Telegram :
`https://amazon-affiliate-bot.onrender.com/webhook/<WEBHOOK_SECRET>`.

Vérifiez dans les logs Render le message `✅ Webhook registered: ...`.

## Étape 4 — Secrets GitHub (trigger quotidien)

Les workflows GitHub utilisent `RENDER_APP_URL` et `TRIGGER_SECRET` :

```powershell
gh secret set RENDER_APP_URL --body "https://amazon-affiliate-bot.onrender.com" -R MSAAFLYAM/BOT
# TRIGGER_SECRET déjà créé
```

Puis vérifiez :
```powershell
gh secret list -R MSAAFLYAM/BOT
```

## Étape 5 — Vérifier

1. `https://amazon-affiliate-bot.onrender.com/health` → `{"status":"ok", ...}`
2. Dans Telegram, envoyez `/health` au bot
3. Le workflow `daily_trigger.yml` tourne chaque jour à 8:00 UTC
4. Le workflow `keep-alive.yml` ping toutes les 10 min (anti-sleep)

---

## Limites connues (tier gratuit Render)

| Limite | Impact | Solution |
|--------|--------|----------|
| Spin-down 15 min sans trafic | Bot en veille, réveil ~1 min | `keep-alive.yml` toutes les 10 min |
| Filesystem éphémère | SQLite/cache perdus au redéploiement | Accepté (Blogger est la source de vérité) |
| 0.1 CPU / 512 MB | Pipeline de scraping plus lent | Suffisant pour ~5 articles/jour |
| `AUTO_DISCOVER_ENABLED=false` | Pas de scheduler interne | Le trigger quotidien GitHub s'en charge |

---

## Rollback / arrêt

- **Redéployer** : Dashboard → service → "Manual Deploy" → "Deploy latest commit"
- **Stopper** : "Suspend Service" (enregistre les heures gratuites)