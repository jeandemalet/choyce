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

## Travaux en arrière-plan

Dès qu'un travail dure, une petite barre en bas à gauche de la fenêtre indique où il en est : ouverture d'un projet, chargement des aperçus, analyse des couleurs, envoi à la corbeille. Les travaux de moins d'une seconde n'affichent rien. L'export, le déplacement du portfolio et le téléchargement d'une mise à jour gardent leur propre barre, avec leur bouton Annuler quand ils en ont un.

## Les vues

Le bandeau du haut porte, au centre, les six vues : **Classique**, Grille, Pêle-mêle, Couleur, Tindr et Instagram. La touche `G` bascule entre Classique et la dernière autre vue, et `Ctrl+0` à `Ctrl+5` ouvrent chacune d'elles.

**‹ Accueil**, en haut à gauche, revient à l'écran d'accueil depuis n'importe quelle vue. **Filtre**, à côté des vues, rappelle le filtre actif et déplie au survol un panneau, qui se replie quand la souris le quitte. Il donne le filtre par note (par exemple « 5 ★ », ou « 3 ★ » et « et + », chaque note indiquant en tout petit son nombre de photos) le filtre par marqueur, et les filtres Portfolio et À publier, qui s'ajoutent aux autres. Hors de la vue Classique, il donne aussi le noir et blanc pour juger les contrastes, la couleur du fond et le zoom : `−` et `+`, et un clic sur le pourcentage revient à la valeur par défaut, comme la touche `0`.

Le menu **Projet** ouvre un dossier, liste les projets récents et retrouve les anciens projets d'un disque. L'accueil les présente aussi sous forme de cartes. Un projet dont le disque est débranché reste dans la liste.

## La vue Classique

Une photo en grand et le ruban de toutes les photos à droite. Ajustée, la photo s'arrête au-dessus de la petite barre qui apparaît au mouvement de la souris et donne accès aux étoiles, au marqueur, au portfolio, à « à publier » et à la couleur du fond : cinq gris ou n'importe quelle autre couleur avec le bouton +. Le clic droit et la touche `B` changent aussi le fond. La molette zoome depuis le centre de la vue, et on attrape l'image à la souris pour la déplacer, quel que soit le zoom. Le zoom et le déplacement restent d'une photo à l'autre, et `0` recentre et revient à l'ajustement. En arrière-plan, Vision Board prépare les images nettes des dix photos suivantes, dans le sens où vous avancez, et de deux photos derrière.

## Les autres vues

La barre du bas porte les réglages propres à la vue et, dès qu'une photo est sélectionnée, sa note ; un clic dans le vide retire la sélection. On annule avec `Ctrl+Z`, et un double-clic ouvre la photo dans la vue Classique.

## Sélection multiple

`Ctrl+clic` ajoute une photo à la sélection et `Ctrl+A` sélectionne toutes celles que laisse passer le filtre, sans celles qui sont sorties de la grille. Une note, un marqueur, un cœur, le portfolio ou « à publier » s'appliquent alors à toute la sélection, au clavier, depuis la barre comme d'un clic sur l'une des photos sélectionnées ; une bascule part de l'état de la photo affichée. `Ctrl+Z` annule le tout en une fois. `Ctrl+D` désélectionne : la vue Classique garde seulement la photo affichée. Tindr et Instagram montrent une photo à la fois et n'ont donc pas de sélection. Dans le portfolio, retirer plusieurs photos demande confirmation.

| Vue | Principe | Réglages en bas |
| --- | --- | --- |
| **Grille** | Une grille décalée, comme une galerie éditoriale, ou des rangées. En décalé, les étoiles se placent sous la photo ; en rangées, sur la photo. Formats égaux cale les rangées sur l'orientation majoritaire, Horizontales en avant sur la hauteur des horizontales, Verticales en avant sur celle des verticales, sans changer l'ordre des photos. Tirez une photo hors de la grille, sur un côté ou en bas, pour la sortir de la sélection. `Ctrl` + molette change la taille. | Disposition, formats mis en avant (rangées seulement), largeur du panneau central, mélange, action de sortie |
| **Pêle-mêle** | Toutes les photos du filtre posées sur une table, les mieux notées plus grandes. La table tient en largeur et continue vers le bas ; on glisse le fond pour s'y promener. On déplace les photos à la souris, la molette monte et descend, et le zoom se règle en haut ou avec `Ctrl` + molette. | Mélanger (touche `R`), photos droites par défaut ou penchées, curseur de 2 à 16 photos en largeur |
| **Couleur** | Un mur de photos rangées selon leur couleur dominante, les images neutres et le noir et blanc à la fin. Les blocs de couleur séparent les rouges, oranges, jaunes, etc., chacun sous son titre. Le curseur de défilement suit uniquement ce qui est trié : la teinte seule, à saturation et luminosité fixes, en tri par couleur ; du noir au blanc en luminosité et en contraste ; du gris au vif en saturation. | Disposition décalée ou en rangées, blocs de couleur (tri par couleur seulement), tri par couleur, luminosité, contraste ou saturation, et son sens |
| **Tindr** | Une photo à la fois : glissez-la à droite ou à gauche, ou utilisez les touches précédente et suivante. Espace passe sans décider, Retour arrière revient en arrière. La molette zoome et le zoom se conserve d'une photo à l'autre, comme dans la vue Classique. Zoomé au-delà de 100 %, glisser déplace la photo et ce sont les touches qui décident. | Action à gauche, action à droite |
| **Instagram** | Les photos comme dans un carrousel : un cadre unique, où une verticale occupe presque toute la hauteur et où une horizontale, de même largeur, sort plus petite, avec le compte au-dessus, les boutons en dessous, le compteur et les points. Flèches, touches ou glisser pour avancer. La molette zoome dans le cadre et dézoome le carrousel entier. Le cœur est le j'aime, l'avion « à publier » et le signet le portfolio. | Nom du compte affiché, seulement les photos à publier |

Une photo sortie de la grille reçoit l'action choisie. « Réafficher » remet les photos sorties à l'écran sans toucher à leurs notes, et `Ctrl+Z` annule la dernière sortie, note comprise.

## Sélection custom

Une sélection custom rassemble des photos de plusieurs galeries pour les ranger dans des buckets, par exemple un par concours. Les sélections se trouvent sur l'accueil, sous les projets récents, chacune avec son nom et une de ses photos. On y revient depuis un projet par **‹ Accueil**, en haut à gauche, par le menu Projet ou `Ctrl+6`, qui ferment ce projet. On peut en créer autant qu'on veut, et tout s'enregistre au fur et à mesure.

Une nouvelle sélection demande d'abord son nom et ses galeries : aucune n'est cochée au départ, un clic n'importe où sur une ligne la coche, et des boutons prennent toutes les galeries ou seulement le portfolio. Chacune a ses filtres de note, de marqueur et « à publier ». Une photo présente dans deux galeries ne compte qu'une fois, et une photo rejetée n'entre que si le filtre demande les rejetées. « Modifier la sélection » revient sur ce choix sans perdre les photos déjà rangées.

La photo occupe le centre et les buckets le bas de l'écran. On passe d'une photo à l'autre avec les flèches de l'écran, qui apparaissent sous la souris, avec celles du clavier, ou avec `C` et `V`. La molette, `+` et `-` zooment, et le zoom reste d'une photo à l'autre ; `0` ou un clic sur le pourcentage revient à l'ajustement. Le fond se change dans la bande du haut, d'un clic droit ou avec `B`, et cette bande donne aussi le nom de la photo.

On glisse la photo sur un bucket pour l'y ranger et passer à la suivante. Zoomé, glisser déplace la photo, et l'amener jusqu'au bas de la vue la prend pour la ranger. Pour ne rien en faire, il suffit de passer à la suivante. Une photo peut aller dans plusieurs buckets. Les touches `1` à `5` rangent aussi dans les cinq premiers, `Maj+C` retire la photo de ses buckets et `Ctrl+Z` annule. Un clic sur un bucket y met la photo ou l'en retire sans changer de photo.

Chaque bucket a un titre, s'il le faut une description, et une couleur pastel en contour : une couleur libre lui est donnée par défaut et se change parmi dix. Les trois points en haut à droite, ou un clic droit, permettent de voir ses photos, de le modifier ou de le supprimer. « Voir les photos » les montre toutes en grand, et une croix sur chacune la retire du bucket ; rien ne bouge sur le disque.

Une sélection s'ouvre aussi dans les autres vues, comme un projet : le bouton **Buckets** du bandeau revient au tri, et Classique, Grille, Pêle-mêle, Couleur, Tindr et Instagram montrent ses photos, avec le nom de leurs buckets sur chacune. La bande du bas montre tous les buckets : un clic n'affiche que celui-là, ce qui permet de les revoir un par un, et « Toutes » les remet toutes ; **Filtre** propose aussi les photos sans bucket. Dans la Grille, Couleur et Pêle-mêle, on glisse une photo sur cette bande : la grande partie d'une zone la déplace vers ce bucket, la petite, « + Aussi », l'y ajoute sans la retirer des autres, et « Retirer du bucket » l'enlève. Vue à travers un bucket, elle ne quitte que celui-là. Les photos sélectionnées avec `Ctrl+clic` ou `Ctrl+A` suivent ensemble. Ici, les touches `1` à `5` mettent la photo dans un bucket ou l'en retirent, `Maj+C` la retire de tous et `Ctrl+Z` annule. Les notes et marqueurs mis ici restent propres à la sélection.

Rien ne bouge non plus avant **Exporter la sélection…** : on choisit un dossier, qui reçoit un dossier au nom de la sélection, avec un sous-dossier par bucket portant son titre. Ce sont des copies exactes des originaux. Un export précédent n'est jamais remplacé : le nouveau prend un nom daté.

Les sélections sont des fichiers du dossier de données du programme, `%LOCALAPPDATA%\Vision Board` sous Windows.

## Vides et retraits

Une photo qui quitte la Grille ou Couleur laisse sa place vide, pour ne pas déplacer les autres sous vos yeux. « Resserrer » referme les vides, tout comme un changement de filtre ou de mode.

## Retirer les étoiles

Partout où une photo affiche ses étoiles, une étoile vide et barrée à gauche la remet à zéro d'un clic : sur les photos de Pêle-mêle, Couleur et Grille, dans la barre de note et dans le ruban de la vue Classique. Avec un filtre « 5 ★ », la photo quitte aussitôt la vue, ce qui permet de resserrer une sélection très vite. `Ctrl+Z` la rétablit.

## J'aime

Un petit cœur en haut à droite de chaque photo se remplit d'un clic ou avec la touche `L`. `Ctrl+Z` l'annule, et il est enregistré avec les notes.

## Portfolio

Le portfolio rassemble vos meilleures photos, tous projets confondus. Le signet de la barre flottante, celui de chaque vignette du ruban de la vue Classique ou la touche `K` y ajoute la photo, et un second appui l'en retire ; `Ctrl+Z` annule. Au premier ajout, Vision Board demande où placer son dossier, qui reçoit sa liste, ses notes et ses exports. L'emplacement se change ensuite dans les Préférences, le menu Projet ou d'un clic droit sur la carte du portfolio : le portfolio y est reporté et l'ancien dossier reste en place.

Ajouter une photo ne copie rien : le portfolio retient seulement où elle se trouve, et aucun fichier n'est écrit avant un export. L'original n'est jamais touché, et retirer une photo l'enlève simplement de la liste. Le bouton **Exporter…** du portfolio copie ses photos, toutes par défaut ou selon les réglages choisis, dans un sous-dossier par projet. Le portfolio s'ouvre depuis sa carte, en tête de l'accueil, comme un projet à part : on y retrouve toutes les vues et les filtres, plus un choix du projet d'origine dans **Filtre**. Une photo y arrive avec ses notes, puis on la note librement sans que cela change le projet d'origine. Si un original est introuvable, par exemple sur un disque débranché, la photo disparaît de la vue et Vision Board le signale, mais elle reste dans le portfolio avec ses notes, et revient quand le fichier est de nouveau là.

Les copies faites par les versions précédentes restent dans le dossier et servent toujours d'images : une copie retirée part à la corbeille, et une copie supprimée à la main sort du portfolio à la prochaine ouverture d'un projet. Une photo déposée à la main dans le dossier y compte comme une photo ordinaire.

## À publier

L'avion de la barre flottante ou la touche `I` marque une photo à publier. C'est une information de plus, indépendante des étoiles, du marqueur et du portfolio, et un filtre de plus.

## Export

**Exporter…** demande quelles photos de la galerie copier : notes, marqueur, portfolio, à publier et, dans le portfolio, projet d'origine. Le dialogue part des filtres en cours, ou des 5 étoiles s'il n'y en a aucun, et compte les photos concernées. Il prend toute la galerie, pas seulement ce qui est à l'écran, et une photo rejetée n'en sort que si on demande les rejetées.

Les copies vont dans `Exportation`, dans un sous-dossier au nom des réglages, par exemple `5 étoiles` ou `4 étoiles et plus, À publier`, ou `Instagram` depuis cette vue. Ce nom se change librement. Une barre de progression suit l'export, et Annuler l'interrompt sans rien laisser. Si ce sous-dossier contient déjà un export, Vision Board demande s'il faut le remplacer ou ranger le nouveau dans un sous-dossier daté, qu'un remplacement ultérieur ne touche pas. Les copies passent par un dossier de travail : un export interrompu laisse le précédent intact.

## Marquage

Les Préférences proposent deux systèmes : étoiles et drapeau, ou étoiles et Retenue, Rejetée, Neutre, comme Lightroom ou Capture One. Un drapeau et une photo retenue sont la même information, si bien que changer de système ne perd rien. Une photo rejetée n'est exportée que si on demande les rejetées.

## Raccourcis

Tous les raccourcis se changent dans **Préférences** (`Ctrl+,`). Les touches en conflit sont signalées. Valeurs par défaut :

| Touche | Commande |
| --- | --- |
| `C`, gauche, haut / `V`, droite, bas | Photo précédente / suivante (en Tindr, haut et bas n'ont pas d'effet) |
| `1` à `5`, `Maj+C`, `Maj+V` | Note, zéro étoile, cinq étoiles |
| `F`, `L`, `K`, `I` | Drapeau, j'aime, portfolio, à publier |
| `P`, `X`, `U` | Retenir, rejeter, neutre |
| `Ctrl+A`, `Ctrl+D` | Sélectionner toutes les photos du filtre, désélectionner |
| `Ctrl+Z` | Annuler la dernière note |
| `G`, Échap | Basculer de vue, revenir à Classique |
| `Ctrl+0` à `Ctrl+5` | Vues |
| `Ctrl+6` | Accueil et sélections custom |
| `B` | Couleur de fond suivante |
| `0`, `+`, `-` | Ajuster, zoomer, dézoomer |
| `F5`, `Ctrl+O`, `Ctrl+W` | Actualiser, ouvrir, fermer le projet |

## Ce qui est écrit sur le disque

Les originaux ne sont jamais réécrits, renommés, déplacés ni supprimés. Tout le reste va dans un dossier `TriPhoto_Session` à la racine du projet, qui garde l'ancien nom de l'application pour que les projets existants s'ouvrent tels quels :

- `session.json` contient les notes, les marqueurs et « à publier » ;
- `session.v1.bak.json` est la copie de l'ancienne session, faite une seule fois à sa première ouverture ;
- `review_state.json` garde les filtres et les photos sorties de la grille, retrouvés à la réouverture du projet ;
- `colors.json` garde les formats, les couleurs dominantes, le contraste et la saturation, calculés une seule fois en arrière-plan ;
- `Previews` contient les aperçus, limités selon la taille de la galerie, entre 512 Mio et 4 Gio ;
- `Exportation` reçoit des copies exactes des originaux, un sous-dossier par export ;
- `Export_5_Etoiles` et `Export_Retenues`, s'ils existent, sont les exports des versions précédentes, laissés à leur place.

Le dossier du portfolio contient son propre `TriPhoto_Session`, où `portfolio.json` retient l'emplacement de chaque photo et où `Exportation` reçoit ses exports, ainsi que les copies faites par les versions précédentes, par projet.

Les galeries créées avec une ancienne version s'ouvrent telles quelles. Une ancienne version peut aussi relire une session récente, mais elle y verra les photos rejetées comme neutres.

Formats pris en charge : JPEG, PNG, TIFF, WebP et BMP. Les fichiers RAW ne sont pas lus.

## Organisation du code

- `triphoto.py` : session, couleurs, export et ancienne interface Tk ;
- `triphoto_qt.py` : fenêtre principale et vue Classique ;
- `triphoto_review.py` : autres vues et analyse des couleurs ;
- `assets/staggered_modules.json` : proportions des modules de la grille décalée ;
- `triphoto_custom.py` : sessions Custom, buckets et leur export ;
- `triphoto_portfolio.py` : liste du portfolio et ouverture de ses photos ;
- `triphoto_prefs.py` : projets récents, raccourcis et préférences ;
- `triphoto_update.py` : détection et installation des nouvelles versions ;
- `triphoto_theme.py` : palette, feuille de style et icônes vectorielles.

Les tests travaillent sur des images de synthèse, avec des réglages séparés des vôtres :

```bash
py -3 tests/test_triphoto.py
```
