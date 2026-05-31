# Mise à jour repo ha-joyonway-p23b32 - 17/05/2026 v3

## 🆕 Nouveautés v3 vs v2

- 🔥 Section **⚠️ Safety & Current Limitations** ÉTOFFÉE :
  - Liste explicite des **4 dangers** au stade actuel de développement
  - Tableau **DO/DON'T enrichi** avec cas concrets
  - Liste des **5 frames capturées et validées** (table °C / °F / script)
  - Mention spécifique du **climate slider** et de ses pièges
  - Section "What this integration does NOT do (yet)"
  - Guidelines pour les futurs **contributeurs**
- ➕ Crédit @KDy enrichi dans Credits section

## 📦 Contenu du ZIP

```
ha-joyonway-update/
├── INSTRUCTIONS.md                        ← CE FICHIER
├── README_PATCH.md                        ← snippet bilingue enrichi
└── packages/
    └── spa_consigne_lock.yaml             ← package YAML (inchangé v2)
```

## 🚀 Procédure GitHub Desktop (8 min)

### Étape 1 — Localiser ton repo local

1. Ouvre **GitHub Desktop**
2. Sélectionne le repo `ha-joyonway-p23b32`
3. Clique **File → Show in Explorer** (Windows) ou **Show in Finder** (Mac)

### Étape 2 — Ajouter le dossier packages

1. Depuis ce ZIP, copie le dossier `packages/`
2. Colle-le à la racine de ton repo local
3. Résultat : `ha-joyonway-p23b32/packages/spa_consigne_lock.yaml`

### Étape 3 — Update README.md (2 modifs)

#### 3a — Insérer les 2 nouvelles sections

1. Ouvre `README.md` dans un éditeur (VSCode, Notepad++, etc.)
2. Cherche la ligne `## Dashboard example` (vers ligne 344)
3. Ouvre `README_PATCH.md` de ce ZIP
4. Copie le contenu entre `==== DÉBUT À COPIER ====` et `==== FIN À COPIER ====`
5. Colle dans `README.md` **JUSTE AVANT** `## Dashboard example`

#### 3b — Update la section Credits (ligne ~454)

Si @KDy existe déjà dans Credits, remplace son rôle par :
```
P25B85 controller reverse-engineering, filtration parsing reference, **CRC safety warning that shaped this repo's safety section**
```

S'il n'existe pas, ajoute une ligne dans le tableau Credits :
```
| [@KDy](https://community.home-assistant.io/u/kdy) | P25B85 controller reverse-engineering, filtration parsing reference, **CRC safety warning that shaped this repo's safety section** |
```

4. Sauvegarde `README.md`

### Étape 4 — Commit dans GitHub Desktop

1. Retourne dans GitHub Desktop
2. Tu vois 2 changements :
   - 🟢 NEW: `packages/spa_consigne_lock.yaml`
   - 🟡 MODIFIED: `README.md`
3. Remplis :
   - **Summary** : `feat: setpoint lock + safety/limitations section (credits @Gaet78, @KDy)`
   - **Description** : copie depuis README_PATCH.md (bas du fichier)
4. **Commit to main**

### Étape 5 — Push

- Clique **Push origin**
- Attends la confirmation

### Étape 6 — Vérification finale

1. https://github.com/KnapTheBuilder/ha-joyonway-p23b32
2. Vérifie que `packages/spa_consigne_lock.yaml` apparaît
3. Vérifie dans le README :
   - ✅ Section **⚠️ Safety & Current Limitations** avec liste des 4 dangers
   - ✅ Tableau **DO / DON'T** complet
   - ✅ Table des **5 frames validées** (15/30/37/38/39 °C)
   - ✅ Section **Setpoint command lock (30s)**
   - ✅ Section **Credits** avec @KDy mis à jour
4. Clique sur le lien `packages/spa_consigne_lock.yaml` dans le README

## 🛡️ Pourquoi cette section est CRUCIALE

À ce stade de développement public, le repo doit être TRANSPARENT sur :
1. ✅ Ce qu'il fait bien (5 frames validées, fonctionne 24/7)
2. ⚠️ Ce qu'il ne fait pas (CRC computation, slider full range)
3. 🚨 Les pièges à éviter (CRC brute-force, frame crafting)

Cela protège :
- **Toi** d'éventuelles plaintes "ça a abîmé mon spa"
- **Les utilisateurs** de mauvaises surprises avec leur installation
- **La communauté** d'une mauvaise réputation pour ce travail collectif

## ✅ Tu peux dormir tranquille

Cette mise à jour est responsable, complète, et crédite les contributeurs.

🌙 Bonne nuit Christophe — c'est un vrai travail communautaire pro.
