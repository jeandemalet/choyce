# Vision Board

Vision Board sert à trier et à revoir les photos d'un reportage, sans jamais toucher aux originaux. Il gère les profils ICC et l'écran sur lequel il s'affiche, charge vite de très grandes galeries et se pilote entièrement au clavier.

## Installation

Sous Windows, le plus simple est de télécharger `VisionBoard-Windows.zip` depuis la [dernière version](https://github.com/jeandemalet/vision-board/releases/latest), de le décompresser et d'ouvrir `VisionBoard.exe`. Le programme n'étant pas signé, Windows peut afficher un avertissement au premier lancement : « Informations complémentaires », puis « Exécuter quand même ».

Pour le lancer depuis les sources, il faut Python 3.10 ou plus récent. Dans le dossier de Vision Board :

```bash
py -3 -m pip install -r requirements.txt
```

Sous Windows, double-cliquez ensuite sur `Lancer Vision Board.bat`. Ailleurs, lancez `python3 triphoto_qt.py`. La gestion du profil de l'écran n'est active que sous Windows : sur les autres systèmes, l'affichage part du principe que l'écran est en sRGB.

## Mises à jour

Quelques secondes après son ouverture, le programme demande à GitHub si une version plus récente existe. Si c'est le cas, il propose de la télécharger en arrière-plan, puis de redémarrer dessus. Vos projets ne sont pas touchés, et sans connexion il ne se passe rien. Lancé depuis les sources, il signale seulement la nouvelle version.

Pour publier une version : changer `APP_VERSION` dans `triphoto.py`, commiter, puis pousser l'étiquette correspondante, par exemple `v1.0.0`. GitHub teste, construit et publie le programme Windows.

## Les vues

Le bandeau du haut porte, au centre, les cinq vues : **Classique**, Grille, Toile, Couleur et Swipe. La touche `G` bascule entre Classique et la dernière autre vue, et `Ctrl+0` à `Ctrl+4` ouvrent chacune d'elles.

**Filtre**, à côté des vues, rappelle le filtre actif et déplie au survol un panneau, qui se replie quand la souris le quitte. Il donne le filtre par note (par exemple « 5 ★ », ou « 3 ★ » et « et + », chaque note indiquant en tout petit son nombre de photos) et le filtre par marqueur. Hors de la vue Classique, il donne aussi le noir et blanc pour juger les contrastes, la couleur du fond et le zoom : `−` et `+`, et un clic sur le pourcentage revient à la valeur par défaut, comme la touche `0`.

Le menu **Projet** ouvre un dossier, liste les projets récents et retrouve les anciens projets d'un disque. L'accueil les présente aussi sous forme de cartes. Un projet dont le disque est débranché reste dans la liste.

## La vue Classique

Une photo en grand et le ruban de toutes les photos à droite. Au mouvement de la souris sur la photo, une petite barre en bas donne accès aux étoiles, au marqueur et à la couleur du fond : cinq gris ou n'importe quelle autre couleur avec le bouton +. Le clic droit et la touche `B` changent aussi le fond. La molette zoome depuis le centre de la vue, et on attrape l'image à la souris pour la déplacer, quel que soit le zoom. Le zoom et le déplacement restent d'une photo à l'autre, et `0` recentre et revient à l'ajustement. En arrière-plan, Vision Board prépare les images nettes des dix photos suivantes, dans le sens où vous avancez, et de deux photos derrière.

## Les autres vues

La barre du bas porte les réglages propres à la vue et, dès qu'une photo est sélectionnée, sa note ; un clic dans le vide retire la sélection. On annule avec `Ctrl+Z`, et un double-clic ouvre la photo dans la vue Classique.

| Vue | Principe | Réglages en bas |
| --- | --- | --- |
| **Grille** | Une grille décalée, comme une galerie éditoriale, ou des rangées. Formats égaux cale les rangées sur l'orientation majoritaire, Horizontales en avant sur la hauteur des horizontales, Verticales en avant sur celle des verticales, sans changer l'ordre des photos. Tirez une photo hors de la grille, sur un côté ou en bas, pour la sortir de la sélection. `Ctrl` + molette change la taille. | Disposition, formats mis en avant (rangées seulement), largeur du panneau central, mélange, action de sortie |
| **Toile** | Toutes les photos du filtre posées sur une table, les mieux notées plus grandes. La table tient en largeur et continue vers le bas ; on glisse le fond pour s'y promener. On déplace les photos à la souris, la molette monte et descend, et le zoom se règle en haut ou avec `Ctrl` + molette. | Mélanger (touche `R`), photos droites ou penchées, curseur de 2 à 16 photos en largeur |
| **Couleur** | Un mur de photos rangées selon leur couleur dominante, les images neutres et le noir et blanc à la fin. Les blocs de couleur séparent les rouges, oranges, jaunes, etc., chacun sous son titre. Le curseur de défilement prend la couleur des photos au milieu de la page. | Disposition décalée ou en rangées, blocs de couleur, tri par couleur ou par contraste (ici la clarté) et son sens |
| **Swipe** | Une photo à la fois : glissez-la à droite ou à gauche, ou utilisez les touches précédente et suivante. Espace passe sans décider, Retour arrière revient en arrière. La molette zoome et le zoom se conserve d'une photo à l'autre, comme dans le Tri. Zoomé au-delà de 100 %, glisser déplace la photo et ce sont les touches qui décident. | Action à gauche, action à droite |

Une photo sortie de la grille reçoit l'action choisie. « Réafficher » remet les photos sorties à l'écran sans toucher à leurs notes, et `Ctrl+Z` annule la dernière sortie, note comprise.

## Vides et retraits

Une photo qui quitte la Grille ou Couleur laisse sa place vide, pour ne pas déplacer les autres sous vos yeux. « Resserrer » referme les vides, tout comme un changement de filtre ou de mode.

## Retirer les étoiles

Partout où une photo affiche ses étoiles, une étoile vide et barrée à gauche la remet à zéro d'un clic : sur les photos de Toile, Couleur et Grille, dans la barre de note et dans le ruban du Tri. Avec un filtre « 5 ★ », la photo quitte aussitôt la vue, ce qui permet de resserrer une sélection très vite. `Ctrl+Z` la rétablit.

## J'aime

Un petit cœur en haut à droite de chaque photo se remplit d'un clic ou avec la touche `L`. `Ctrl+Z` l'annule, et il est enregistré avec les notes.

## Export

Une barre de progression suit l'export, et Annuler l'interrompt sans rien laisser. Si un export existe déjà, Vision Board demande s'il faut le remplacer ou ranger le nouveau dans un sous-dossier daté, qu'un remplacement ultérieur ne touche pas. Les copies passent par un dossier de travail : un export interrompu laisse le précédent intact.

## Marquage

Les Préférences proposent deux systèmes : étoiles et drapeau, ou étoiles et Retenue, Rejetée, Neutre, comme Lightroom ou Capture One. Un drapeau et une photo retenue sont la même information, si bien que changer de système ne perd rien. Une photo rejetée n'est jamais exportée avec les cinq étoiles.

## Raccourcis

Tous les raccourcis se changent dans **Préférences** (`Ctrl+,`). Les touches en conflit sont signalées. Valeurs par défaut :

| Touche | Commande |
| --- | --- |
| `C`, gauche, haut / `V`, droite, bas | Photo précédente / suivante (en Swipe, haut et bas n'ont pas d'effet) |
| `1` à `5`, `Maj+C`, `Maj+V` | Note, zéro étoile, cinq étoiles |
| `F`, `L` | Drapeau, j'aime |
| `P`, `X`, `U` | Retenir, rejeter, neutre |
| `Ctrl+Z` | Annuler la dernière note |
| `G`, Échap | Basculer de vue, revenir à Classique |
| `Ctrl+0` à `Ctrl+4` | Vues |
| `B` | Couleur de fond suivante |
| `0`, `+`, `-` | Ajuster, zoomer, dézoomer |
| `F5`, `Ctrl+O`, `Ctrl+W` | Actualiser, ouvrir, fermer le projet |

## Ce qui est écrit sur le disque

Les originaux ne sont jamais réécrits, renommés, déplacés ni supprimés. Tout le reste va dans un dossier `TriPhoto_Session` à la racine du projet, qui garde l'ancien nom de l'application pour que les projets existants s'ouvrent tels quels :

- `session.json` contient les notes et les marqueurs ;
- `session.v1.bak.json` est la copie de l'ancienne session, faite une seule fois à sa première ouverture ;
- `review_state.json` garde les filtres et les photos sorties de la grille, retrouvés à la réouverture du projet ;
- `colors.json` garde les formats et les couleurs dominantes, calculés une seule fois en arrière-plan ;
- `Previews` contient les aperçus, limités selon la taille de la galerie, entre 512 Mio et 4 Gio ;
- `Export_5_Etoiles` et `Export_Retenues` reçoivent des copies exactes des originaux.

Les galeries créées avec une ancienne version s'ouvrent telles quelles. Une ancienne version peut aussi relire une session récente, mais elle y verra les photos rejetées comme neutres.

Formats pris en charge : JPEG, PNG, TIFF, WebP et BMP. Les fichiers RAW ne sont pas lus.

## Organisation du code

- `triphoto.py` : session, couleurs, export et ancienne interface Tk ;
- `triphoto_qt.py` : fenêtre principale et vue Classique ;
- `triphoto_review.py` : autres vues et analyse des couleurs ;
- `assets/staggered_modules.json` : proportions des modules de la grille décalée ;
- `triphoto_prefs.py` : projets récents, raccourcis et préférences ;
- `triphoto_update.py` : détection et installation des nouvelles versions ;
- `triphoto_theme.py` : palette, feuille de style et icônes vectorielles.

Les tests travaillent sur des images de synthèse, avec des réglages séparés des vôtres :

```bash
py -3 tests/test_triphoto.py
```
