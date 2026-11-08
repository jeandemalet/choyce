# Choyce

Choyce sert à trier et à revoir les photos d'un reportage, sans jamais toucher aux originaux. Il gère les profils ICC et l'écran sur lequel il s'affiche, charge vite de très grandes galeries et se pilote entièrement au clavier.

## Le parcours

1. **Ajouter des photos.** Un dossier de JPEG forme un projet. Rien n'est copié ni modifié.
2. **Premier tri.** Dans le projet, on note et on marque les photos, dans la vue qu'on préfère, puis les filtres ne gardent que les meilleures.
3. **Sous-tris.** Sous les filtres, « Avec ces photos » les ajoute au portfolio, qui rassemble vos meilleures photos de tous les projets, ou crée une sélection pour un usage précis : un concours, les réseaux sociaux, une commande. Une sélection range ses photos dans des buckets.
4. **Exporter.** **Exporter…**, toujours au même endroit, copie les originaux voulus.
5. **Revenir.** Tout s'enregistre au fur et à mesure. L'accueil présente, dans cet ordre, les projets, le portfolio et les sélections. Un projet rouvre sur sa vue, sa photo et ses filtres, une sélection sur ses buckets et sa dernière photo. Le menu en haut à gauche, qui porte le nom de ce qui est ouvert, passe directement de l'un à l'autre.

## Installation

Sous Windows, le plus simple est de télécharger `Choyce-Windows.zip` depuis la [dernière version](https://github.com/jeandemalet/choyce/releases/latest), de le décompresser et d'ouvrir `Choyce.exe`. Le programme n'étant pas signé, Windows peut afficher un avertissement au premier lancement : « Informations complémentaires », puis « Exécuter quand même ».

Pour le lancer depuis les sources, il faut Python 3.10 ou plus récent. Dans le dossier de Choyce :

```bash
py -3 -m pip install -r requirements.txt
```

Sous Windows, double-cliquez ensuite sur `Lancer Choyce.bat`. Ailleurs, lancez `python3 triphoto_qt.py`. La gestion du profil de l'écran n'est active que sous Windows : sur les autres systèmes, l'affichage part du principe que l'écran est en sRGB.

## Mises à jour

Quelques secondes après son ouverture, le programme demande à GitHub si une version plus récente existe. Si c'est le cas, il propose de la télécharger en arrière-plan, puis de redémarrer dessus. Vos projets ne sont pas touchés, et sans connexion il ne se passe rien. Lancé depuis les sources, il signale seulement la nouvelle version.

Pour publier une version : changer `APP_VERSION` dans `triphoto.py`, commiter, puis pousser l'étiquette correspondante, par exemple `v1.0.0`. GitHub teste, construit et publie le programme Windows.

## Travaux en arrière-plan

Dès qu'un travail dure, une petite barre en bas à gauche de la fenêtre indique où il en est : ouverture d'un projet, chargement des aperçus, analyse des couleurs, envoi à la corbeille. Les travaux de moins d'une seconde n'affichent rien. L'export, le déplacement du portfolio et le téléchargement d'une mise à jour gardent leur propre barre, avec leur bouton Annuler quand ils en ont un.

## Les vues

L'écran se lit en trois panneaux, chacun avec son rôle. En haut, **comment regarder** : les vues. À gauche, **les réglages de la vue affichée** : tri, affichage, disposition, actions. À droite, **quelles photos** : le filtre. La note et les marques de la photo restent en bas, sous la photo.

Le bouton **Buckets**, à côté du filtre, ou `Ctrl+B`, passe en mode buckets, dans un projet, le portfolio ou une sélection. Les buckets s'affichent en bas de toutes les vues, le bouton + en crée un, et les touches `1` à `5` les remplissent au lieu de noter. La vue Classique montre alors la photo en grand, à glisser sur un bucket. Hors de ce mode, les buckets restent visibles sur les photos et dans le filtre. Les buckets d'un projet sont enregistrés avec ses notes et, s'il en a de remplis, l'export propose un dossier par bucket.

Le bandeau du haut porte, au centre, les vues : **Classique**, Grille, Pêle-mêle, Tindr et Instagram. La touche `G` bascule entre Classique et la dernière autre vue, et `Ctrl+0`, `Ctrl+1`, `Ctrl+2`, `Ctrl+4` et `Ctrl+5` ouvrent chacune d'elles, `Ctrl+3` la Grille triée par couleur. Dans les vues, la section Affichage du panneau de gauche donne le noir et blanc, la couleur du fond et le zoom.

**‹ Accueil**, en haut à gauche, revient à l'écran d'accueil depuis n'importe quelle vue. **↶ Annuler**, juste à côté du menu du projet, reprend la dernière action comme `Ctrl+Z` : note, marqueur, portfolio, rangement dans un bucket, sortie de grille, photo déplacée dans Pêle-mêle ou mélange. **↷ Rétablir**, ou `Ctrl+Y`, refait ce qui vient d'être annulé, tant qu'aucune nouvelle action n'a eu lieu.

Le panneau du filtre reste ouvert à droite de la fenêtre, à côté des photos qui lui répondent. **Filtre**, à côté des vues, rappelle le filtre actif et ouvre ou ferme ce panneau ; le choix est retenu. La vue Classique, qui a son ruban à droite, le laisse fermé tant qu'on ne le demande pas. « Tout effacer » retire tous les filtres. Il donne le filtre par note (par exemple « 5 ★ », ou « 3 ★ » et « et + », chaque note indiquant en tout petit son nombre de photos), le filtre par marqueur ou par drapeau, et « Seulement » : Portfolio, À publier et J'aime, qui s'ajoutent aux autres, dans un projet comme dans une sélection.

« Avec ces photos », dans le même panneau, part de ce que les filtres ont gardé. « Ajouter au portfolio » les y met en une fois, et `Ctrl+Z` annule. « Créer une sélection… » ouvre une nouvelle sélection avec ce projet déjà coché et ses filtres repris : il ne reste qu'à lui donner un nom. « Annuler » ramène à la vue qu'on quittait.

Le tri, « Trier par », se trouve dans le panneau de gauche de chaque vue, sauf Pêle-mêle, qui pose les photos au hasard.

Le menu en haut à gauche porte le nom du projet, du portfolio ou de la sélection ouverte. Il ajoute des photos, passe à un autre projet, à une autre sélection ou au portfolio, cherche sur un disque et revient à l'accueil. Un projet dont le disque est débranché reste dans la liste.

Si un projet a été déplacé ou renommé, un clic sur sa carte, ou « Retrouver le dossier… » d'un clic droit, demande où il se trouve. Il suffit d'indiquer un dossier qui le contient : Choyce cherche le projet à l'intérieur, et prévient s'il y en a plusieurs, ou s'il ne trouve que des photos sans leurs notes. Le portfolio et les sélections qui pointent vers ce projet suivent. Pour des photos isolées du portfolio ou d'une sélection, « Retrouver les photos manquantes… » du même menu, présent quand il en manque, les cherche par leur nom dans le dossier choisi.

Après le déplacement de plusieurs dossiers, ou pour retrouver d'anciens projets, « Rechercher sur un disque… », sur l'accueil et dans le même menu, évite de les reprendre un par un : indiquez un disque ou un dossier. Choyce y cherche ce qui est perdu, projets, portfolio et sélections, relie ce qu'il trouve, ajoute à l'accueil les projets qui n'y sont pas encore et dit ce qui manque.

## La vue Classique

Une photo en grand et le ruban de toutes les photos à droite, que l'on élargit en tirant sa bordure : les vignettes grandissent avec lui. Ajustée, la photo s'arrête au-dessus de la petite barre qui apparaît au mouvement de la souris et donne accès aux étoiles, au marqueur, au portfolio, à « à publier » et à la couleur du fond : cinq gris ou n'importe quelle autre couleur avec le bouton +. Le clic droit et la touche `B` changent aussi le fond. La molette zoome depuis le centre de la vue, et on attrape l'image à la souris pour la déplacer, quel que soit le zoom. Le zoom et le déplacement restent d'une photo à l'autre, et `0` recentre et revient à l'ajustement. En arrière-plan, Choyce prépare les images nettes des dix photos suivantes, dans le sens où vous avancez, et de deux photos derrière.

## Les autres vues

La barre du bas porte, dès qu'une photo est sélectionnée, sa note ; un clic dans le vide retire la sélection. On annule avec `Ctrl+Z` ou **↶ Annuler**, et un double-clic ouvre la photo dans la vue Classique. Une touche qui n'a aucune photo sur laquelle agir le dit par un court message. Quand une note fait sortir du filtre la photo sélectionnée dans Pêle-mêle ou dans une grille triée, la sélection s'arrête là, pour que la touche suivante ne tombe pas sur une photo qu'on ne regarde pas ; `Ctrl+Z` la ramène, sélectionnée.

## Sélection multiple

`Ctrl+clic` ajoute une photo à la sélection et `Ctrl+A` sélectionne toutes celles que laisse passer le filtre, sans celles qui sont sorties de la grille. Une note, un marqueur, un cœur, le portfolio ou « à publier » s'appliquent alors à toute la sélection, au clavier, depuis la barre comme d'un clic sur l'une des photos sélectionnées ; une bascule part de l'état de la photo affichée. `Ctrl+Z` annule le tout en une fois. `Ctrl+D` désélectionne : la vue Classique garde seulement la photo affichée. Tindr et Instagram montrent une photo à la fois et n'ont donc pas de sélection. Dans le portfolio, retirer plusieurs photos demande confirmation.

| Vue | Principe | Réglages à gauche |
| --- | --- | --- |
| **Grille** | Une grille décalée, comme une galerie éditoriale, ou des rangées. En décalé, les étoiles se placent sous la photo ; en rangées, sur la photo. Formats égaux cale les rangées sur l'orientation majoritaire, Horizontales en avant sur la hauteur des horizontales, Verticales en avant sur celle des verticales, sans changer l'ordre des photos. Tirez une photo hors de la grille, sur un côté ou en bas, pour la sortir de la sélection. `Ctrl` + molette change la taille. | Disposition, formats mis en avant (rangées seulement), largeur du panneau central, mélange, action de sortie |
| **Pêle-mêle** | Toutes les photos du filtre posées sur une table, les mieux notées plus grandes. Elles sont posées sur un quadrillage invisible, aussi large que la plus large d'entre elles, et chaque rangée est aussi haute que sa photo la plus haute. La table tient en largeur et continue vers le bas ; on glisse le fond pour s'y promener. On déplace les photos à la souris, la molette monte et descend, et le zoom se règle en haut ou avec `Ctrl` + molette. | Mélanger (touche `R`), photos droites par défaut ou penchées, curseur de 2 à 16 photos en largeur |
| **Grille triée** | « Trier par », dans les réglages de la Grille, donne le nom, la date, la couleur, la luminosité, le contraste ou la saturation, en croissant ou décroissant. Hors du nom, c'est un mur de photos rangées selon leur couleur dominante, les images neutres et le noir et blanc à la fin. Les blocs de couleur séparent les rouges, oranges, jaunes, etc., chacun sous son titre. Le curseur de défilement suit uniquement ce qui est trié : la teinte seule, à saturation et luminosité fixes, en tri par couleur ; du noir au blanc en luminosité et en contraste, celui-ci rapporté à ce que permet la luminosité de la photo, pour que les photos de nuit ne passent pas toutes pour les moins contrastées ; du gris au vif en saturation. | Disposition décalée ou en rangées, blocs de couleur (tri par couleur seulement), tri par couleur, luminosité, contraste ou saturation, et son sens |
| **Tindr** | Une photo à la fois : glissez-la à droite ou à gauche, ou utilisez les touches précédente et suivante. Espace passe sans décider, Retour arrière revient en arrière. La molette zoome et le zoom se conserve d'une photo à l'autre, comme dans la vue Classique. Zoomé au-delà de 100 %, glisser déplace la photo et ce sont les touches qui décident. | Action à gauche, action à droite |
| **Instagram** | Dans le panneau de gauche, le choix Mode clair ou Mode sombre vaut indépendamment du fond des autres vues : poste clair sur écran noir, ou l'inverse. Les photos comme dans un carrousel : un cadre unique, où une verticale occupe presque toute la hauteur et où une horizontale, de même largeur, sort plus petite, avec le compte au-dessus, les boutons en dessous, le compteur et les points. Flèches, touches ou glisser pour avancer. La molette zoome dans le cadre et dézoome le carrousel entier. Le cœur est le j'aime, l'avion « à publier » et le signet le portfolio. | Nom du compte affiché |

Une photo sortie de la grille reçoit l'action choisie. « Réafficher » remet les photos sorties à l'écran sans toucher à leurs notes, et `Ctrl+Z` annule la dernière sortie, note comprise.

## Sélections

Une sélection rassemble des photos de plusieurs projets pour les ranger dans des buckets, par exemple un par concours. Les sélections se trouvent sur l'accueil, sous le portfolio, chacune avec son nom et une de ses photos. On y revient par **‹ Accueil**, en haut à gauche, ou directement par le menu voisin. On peut en créer autant qu'on veut, et tout s'enregistre au fur et à mesure.

Une nouvelle sélection demande d'abord son nom et ses projets : aucun n'est coché au départ, sauf si elle part d'un projet ouvert. Un clic n'importe où sur une ligne la coche, et des boutons prennent tous les projets ou seulement le portfolio. Chacun a ses filtres de note, de marqueur, « à publier » et « j'aime ». Une photo présente dans deux projets ne compte qu'une fois, et une photo rejetée n'entre que si le filtre demande les rejetées. « Ajouter des photos d'autres projets… », dans le panneau de gauche ou le menu du projet, revient sur ce choix pour cocher d'autres projets ou le portfolio, sans perdre les photos déjà rangées. En mode buckets, Classique garde le bandeau de photos à droite et le panneau Filtre, avec les buckets en bas.

La photo occupe le centre et les buckets le bas de l'écran. On passe d'une photo à l'autre avec les flèches de l'écran, qui apparaissent sous la souris, avec celles du clavier, ou avec `C` et `V`. La molette, `+` et `-` zooment, et le zoom reste d'une photo à l'autre ; `0` ou un clic sur le pourcentage revient à l'ajustement. Le fond se change dans la bande au-dessus de la photo, d'un clic droit ou avec `B`, et cette bande donne aussi le nom de la photo et son projet.

On glisse la photo sur un bucket pour l'y ranger et passer à la suivante. Zoomé, glisser déplace la photo, et l'amener jusqu'au bas de la vue la prend pour la ranger. Pour ne rien en faire, il suffit de passer à la suivante. Une photo peut aller dans plusieurs buckets. Les touches `1` à `5` rangent aussi dans les cinq premiers, `Maj+C` retire la photo de ses buckets et `Ctrl+Z` annule. Un clic sur un bucket y met la photo ou l'en retire sans changer de photo.

Chaque bucket a un titre, s'il le faut une description, et une couleur pastel en contour : une couleur libre lui est donnée par défaut et se change parmi dix. Les trois points en haut à droite, ou un clic droit, permettent de voir ses photos, de le modifier ou de le supprimer. « Voir les photos » les montre toutes en grand, et une croix sur chacune la retire du bucket ; rien ne bouge sur le disque.

Une sélection s'ouvre comme un projet, en mode buckets : Classique, Grille, Pêle-mêle, Tindr et Instagram montrent ses photos, avec le nom de leurs buckets sur chacune. Le bouton **Buckets** quitte ce mode pour revenir aux notes. Le filtre choisit les buckets affichés : inclure, exclure, ou seulement les photos sans bucket.

La bande du bas porte les buckets et sert à attribuer la photo : un clic l'y met ou l'en retire. Dans la Grille et Pêle-mêle, on peut aussi glisser une photo sur cette bande. Sur un autre bucket, la moitié gauche la déplace et la moitié droite l'y ajoute aussi ; sur un bucket où elle est déjà, un dépôt l'en retire. Dans Tindr, sans bande, un bucket se choisit dans le panneau de gauche : à gauche on l'en retire, à droite on l'y ajoute. Vue à travers un seul bucket, elle ne quitte que celui-là. Les photos sélectionnées avec `Ctrl+clic` ou `Ctrl+A` suivent ensemble. Les touches `1` à `5` font la même chose que le clic, `Maj+C` retire la photo de tous ses buckets et `Ctrl+Z` annule.

Pour relire un bucket, la ligne **Afficher**, toujours en haut sous le bandeau, l'affiche d'un clic dans la vue en cours, de même que « Toutes » et « Sans bucket ». On vérifie ainsi que chaque photo a sa place, dans le format d'affichage qu'on préfère, et on la glisse ailleurs si besoin. Dans la vue Classique, on glisse pour cela sa vignette depuis le ruban.

**Vérifier les photos de la sélection…**, dans le menu en haut à gauche, repère les fichiers introuvables, vides ou illisibles. Elle propose de les retrouver ou de les retirer de la sélection et de ses buckets, sans toucher aux fichiers ; une photo retirée ne revient pas quand on modifie la sélection. Un export ne s'arrête plus sur un fichier abîmé : il copie le reste et les nomme à la fin. Chaque sélection garde une copie de son état précédent, reprise automatiquement si son fichier est endommagé.

**Filtre** choisit ce qui s'affiche, dans toutes ces vues comme dans Buckets. Pour chaque bucket, trois choix : Exclure, Inclure ou Peu importe. Avec plusieurs buckets inclus, on affiche les photos dans l'un d'eux ou dans tous à la fois. Les notes et marqueurs filtrent aussi, et restent propres à la sélection.

Rien ne bouge non plus avant **Exporter…**, le même bouton que pour un projet : on choisit un dossier, qui reçoit un dossier au nom de la sélection, avec un sous-dossier par bucket portant son titre. Ce sont des copies exactes des originaux. Un export précédent n'est jamais remplacé : le nouveau prend un nom daté.

Les sélections sont des fichiers du dossier de données du programme, `%LOCALAPPDATA%\Vision Board` sous Windows, nom gardé de l'ancienne appellation du programme.

## Vides et retraits

Une photo qui quitte la Grille ou Couleur laisse sa place vide, pour ne pas déplacer les autres sous vos yeux. « Resserrer » referme les vides, tout comme un changement de filtre ou de mode.

## Retirer les étoiles

Partout où une photo affiche ses étoiles, une étoile vide et barrée à gauche la remet à zéro d'un clic : sur les photos de Pêle-mêle, Couleur et Grille, dans la barre de note et dans le ruban de la vue Classique. Avec un filtre « 5 ★ », la photo quitte aussitôt la vue, ce qui permet de resserrer une sélection très vite. `Ctrl+Z` la rétablit.

## J'aime

Un petit cœur en haut à droite de chaque photo se remplit d'un clic ou avec la touche `L`. `Ctrl+Z` l'annule, et il est enregistré avec les notes.

## Portfolio

Le portfolio rassemble vos meilleures photos, tous projets confondus. Le signet de la barre flottante, celui de chaque vignette du ruban de la vue Classique ou la touche `K` y ajoute la photo, et un second appui l'en retire ; `Ctrl+Z` annule. Au premier ajout, Choyce demande où placer son dossier, qui reçoit sa liste, ses notes et ses exports. L'emplacement se change ensuite dans les Préférences ou d'un clic droit sur la carte du portfolio : le portfolio y est reporté et l'ancien dossier reste en place.

Ajouter une photo ne copie rien : le portfolio retient seulement où elle se trouve, et aucun fichier n'est écrit avant un export. L'original n'est jamais touché, et retirer une photo l'enlève simplement de la liste. Le bouton **Exporter…** du portfolio copie ses photos, toutes par défaut ou selon les réglages choisis, dans un sous-dossier par projet. Le portfolio s'ouvre depuis sa carte, en tête de l'accueil, comme un projet à part : on y retrouve toutes les vues et les filtres, plus un choix du projet d'origine dans **Filtre**. Une photo y arrive avec ses notes, puis on la note librement sans que cela change le projet d'origine. Si un original est introuvable, par exemple sur un disque débranché, la photo disparaît de la vue et Choyce le signale, mais elle reste dans le portfolio avec ses notes, et revient quand le fichier est de nouveau là.

Les copies faites par les versions précédentes restent dans le dossier et servent toujours d'images : une copie retirée part à la corbeille, et une copie supprimée à la main sort du portfolio à la prochaine ouverture d'un projet. Une photo déposée à la main dans le dossier y compte comme une photo ordinaire.

## À publier

L'avion de la barre flottante ou la touche `I` marque une photo à publier. C'est une information de plus, indépendante des étoiles, du marqueur et du portfolio, et un filtre de plus.

## Export

**Exporter…** demande quelles photos du projet copier : notes, marqueur, portfolio, à publier, j'aime et, dans le portfolio, projet d'origine. Ces réglages se combinent, par exemple J'aime avec « 3 étoiles et plus ». Le dialogue part des filtres en cours, ou des 5 étoiles s'il n'y en a aucun, et compte les photos concernées. Il prend tout le projet, pas seulement ce qui est à l'écran, et une photo rejetée n'en sort que si on demande les rejetées.

Les copies vont dans `Exportation`, dans un sous-dossier au nom des réglages, par exemple `5 étoiles` ou `4 étoiles et plus, À publier`, ou `Instagram` depuis cette vue. Ce nom se change librement. Une barre de progression suit l'export, et Annuler l'interrompt sans rien laisser. Si ce sous-dossier contient déjà un export, Choyce demande s'il faut le remplacer ou ranger le nouveau dans un sous-dossier daté, qu'un remplacement ultérieur ne touche pas. Les copies passent par un dossier de travail : un export interrompu laisse le précédent intact.

Le clic droit sur une photo l'exporte aussitôt, sans dialogue, ainsi que toutes les photos sélectionnées si elle en fait partie ; dans le vide, il exporte la sélection en cours. Cela vaut pour toutes les vues et le ruban. Les copies vont dans `Exportation`, dans un sous-dossier `Sélection` daté qui ne remplace rien. Dans une sélection, elles vont à côté de ses exports, sans sous-dossier par bucket, et son dossier d'export n'est demandé que la première fois.

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
| `Ctrl+6` | Accueil |
| `B` | Couleur de fond suivante |
| `0`, `+`, `-` | Ajuster, zoomer, dézoomer |
| `F5`, `Ctrl+O`, `Ctrl+W` | Actualiser, ouvrir, fermer le projet |

## Ce qui est écrit sur le disque

Les originaux ne sont jamais réécrits, renommés, déplacés ni supprimés. Tout le reste va dans un dossier `TriPhoto_Session` à la racine du projet, qui garde l'ancien nom de l'application pour que les projets existants s'ouvrent tels quels :

- `session.json` contient les notes, les marqueurs et « à publier » ;
- `session.v1.bak.json` est la copie de l'ancienne session, faite une seule fois à sa première ouverture ;
- `review_state.json` garde les filtres, les photos sorties de la grille, la dernière vue et la dernière photo, retrouvés à la réouverture du projet ;
- `colors.json` garde les formats, les couleurs dominantes, le contraste et la saturation, calculés une seule fois en arrière-plan ;
- `Previews` contient les aperçus, limités selon la taille du projet, entre 512 Mio et 4 Gio ;
- `Exportation` reçoit des copies exactes des originaux, un sous-dossier par export ;
- `Export_5_Etoiles` et `Export_Retenues`, s'ils existent, sont les exports des versions précédentes, laissés à leur place.

Le dossier du portfolio contient son propre `TriPhoto_Session`, où `portfolio.json` retient l'emplacement de chaque photo et où `Exportation` reçoit ses exports, ainsi que les copies faites par les versions précédentes, par projet.

Les projets créés avec une ancienne version s'ouvrent tels quels. Une ancienne version peut aussi relire une session récente, mais elle y verra les photos rejetées comme neutres.

Formats pris en charge : JPEG, PNG, TIFF, WebP et BMP. Les fichiers RAW ne sont pas lus.

## Ajouter à et envoyer avec Blip

Le clic droit sur une photo, ou sur toutes celles sélectionnées, propose aussi **Ajouter à…**, pour le portfolio, une sélection existante ou une nouvelle, et **Envoyer avec Blip** : Blip s'ouvre sur un envoi de ces photos, et l'on y choisit le téléphone. **Installer Blip sur un téléphone…** affiche deux QR codes, pour iPhone et pour Android, qui mènent à Blip dans l'App Store ou sur Google Play. Si Blip manque sur l'ordinateur, Choyce propose de l'installer.

## Organisation du code

- `triphoto.py` : session, couleurs, export et ancienne interface Tk ;
- `triphoto_qt.py` : fenêtre principale et vue Classique ;
- `triphoto_review.py` : autres vues et analyse des couleurs ;
- `assets/staggered_modules.json` : proportions des modules de la grille décalée ;
- `triphoto_custom.py` : sélections, buckets et leur export ;
- `triphoto_portfolio.py` : liste du portfolio et ouverture de ses photos ;
- `triphoto_prefs.py` : projets récents, raccourcis et préférences ;
- `triphoto_update.py` : détection et installation des nouvelles versions ;
- `triphoto_blip.py` : envoi des photos avec Blip ;
- `triphoto_theme.py` : palette, feuille de style et icônes vectorielles.

Les tests travaillent sur des images de synthèse, avec des réglages séparés des vôtres :

```bash
py -3 tests/test_triphoto.py
```
