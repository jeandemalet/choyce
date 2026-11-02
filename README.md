# TriPhoto

TriPhoto sert à trier et à revoir les photos d'un reportage, sans jamais toucher aux originaux. Il gère les profils ICC et l'écran sur lequel il s'affiche, charge vite de très grandes galeries et se pilote entièrement au clavier.

## Installation

Il faut Python 3.10 ou plus récent. Dans le dossier de TriPhoto :

```bash
py -3 -m pip install -r requirements.txt
```

Sous Windows, double-cliquez ensuite sur `Lancer TriPhoto.bat`. Ailleurs, lancez `python3 triphoto_qt.py`. La gestion du profil de l'écran n'est active que sous Windows : sur les autres systèmes, l'affichage part du principe que l'écran est en sRGB.

## La page Tri

Le menu **Projet** ouvre un dossier, liste les projets récents et retrouve les anciens projets d'un disque. L'accueil les présente aussi sous forme de cartes. Un projet dont le disque est débranché reste dans la liste.

Le bandeau du haut filtre par note et par marqueur, et trie les photos. Au mouvement de la souris sur la photo, une petite barre donne accès aux étoiles, au marqueur et à la couleur du fond : cinq gris ou n'importe quelle autre couleur avec le bouton +. Le clic droit et la touche `B` changent aussi le fond.

## La page Revoir

La touche `G` bascule entre Tri et Revoir. La barre du haut regroupe ce qui vaut pour tous les modes : le mode, le filtre par note (par exemple « 5 ★ », ou « 3 ★ » et « et + »), le filtre par marqueur, le noir et blanc pour juger les contrastes et la couleur du fond. La barre du bas donne la note de la photo et, autour d'elle, les réglages propres au mode. On annule avec `Ctrl+Z`, et un double-clic ouvre la photo dans la page Tri.

| Mode | Principe | Réglages en bas |
| --- | --- | --- |
| **Toile** | Quelques tirages tirés au hasard et posés sur une table, les mieux notés plus grands. On les déplace à la souris, on navigue en glissant le fond et on zoome à la molette. | Mélanger (nouveau tirage, touche `R`), photos droites ou penchées, nombre de photos |
| **Swipe** | Une photo à la fois : glissez-la à droite ou à gauche, ou utilisez les touches précédente et suivante. Espace passe sans décider, Retour arrière revient en arrière. | Action à gauche, action à droite |
| **Couleur** | Un mur de photos rangées en dégradé selon leur couleur dominante, les images neutres et le noir et blanc à la fin. | Bandeau de couleur dominante, activé ou non |
| **Mosaïque** | Une grille décalée, comme une galerie éditoriale, ou des rangées régulières. Tirez une photo hors de la grille, sur un côté ou en bas, pour la sortir de la sélection. `Ctrl` + molette change la taille. | Disposition, formats mis en avant, largeur du panneau central, mélange, action de sortie |

Une photo sortie de la mosaïque reçoit l'action choisie. « Réafficher » remet les photos sorties à l'écran sans toucher à leurs notes, et `Ctrl+Z` annule la dernière sortie, note comprise.

## Marquage

Les Préférences proposent deux systèmes : étoiles et drapeau, ou étoiles et Retenue, Rejetée, Neutre, comme Lightroom ou Capture One. Un drapeau et une photo retenue sont la même information, si bien que changer de système ne perd rien. Une photo rejetée n'est jamais exportée avec les cinq étoiles.

## Raccourcis

Tous les raccourcis se changent dans **Préférences** (`Ctrl+,`). Les touches en conflit sont signalées. Valeurs par défaut :

| Touche | Commande |
| --- | --- |
| `C`, gauche / `V`, droite | Photo précédente / suivante |
| `1` à `5`, `Maj+C`, `Maj+V` | Note, zéro étoile, cinq étoiles |
| `F` | Drapeau |
| `P`, `X`, `U` | Retenir, rejeter, neutre |
| `Ctrl+Z` | Annuler la dernière note |
| `G`, Échap | Basculer vers Revoir, revenir au Tri |
| `Ctrl+1` à `Ctrl+4` | Modes de Revoir |
| `B` | Couleur de fond suivante |
| `0`, `+`, `-` | Ajuster, zoomer, dézoomer |
| `F5`, `Ctrl+O`, `Ctrl+W` | Actualiser, ouvrir, fermer le projet |

## Ce qui est écrit sur le disque

Les originaux ne sont jamais réécrits, renommés, déplacés ni supprimés. Tout le reste va dans un dossier `TriPhoto_Session` à la racine du projet :

- `session.json` contient les notes et les marqueurs ;
- `session.v1.bak.json` est la copie de l'ancienne session, faite une seule fois à sa première ouverture ;
- `colors.json` garde les formats et les couleurs dominantes, calculés une seule fois en arrière-plan ;
- `Previews` contient les aperçus, limités à 512 Mio ;
- `Export_5_Etoiles` et `Export_Retenues` reçoivent des copies exactes des originaux.

Les galeries créées avec une ancienne version s'ouvrent telles quelles. Une ancienne version peut aussi relire une session récente, mais elle y verra les photos rejetées comme neutres.

Formats pris en charge : JPEG, PNG, TIFF, WebP et BMP. Les fichiers RAW ne sont pas lus.

## Organisation du code

- `triphoto.py` : session, couleurs, export et ancienne interface Tk ;
- `triphoto_qt.py` : fenêtre principale et page Tri ;
- `triphoto_review.py` : page Revoir et analyse des couleurs ;
- `assets/staggered_modules.json` : proportions des modules de la grille décalée ;
- `triphoto_prefs.py` : projets récents, raccourcis et préférences ;
- `triphoto_theme.py` : palette, feuille de style et icônes vectorielles.

Les tests travaillent sur des images de synthèse, avec des réglages séparés des vôtres :

```bash
py -3 tests/test_triphoto.py
```
