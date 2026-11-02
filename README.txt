TRIPHOTO

Double-cliquer sur « Lancer TriPhoto.bat ».

Interface Qt adaptée aux écrans haute résolution. Un seul bandeau supérieur contient tri, notes, drapeaux, ouverture et export. Le ruban droit est redimensionnable ; aucun bandeau inférieur.

Raccourcis
C / gauche : précédente. V / droite : suivante.
Maj C : zéro étoile. Maj V : cinq étoiles.
1 à 5 : note correspondante. F : drapeau. F5 : actualiser.
0 : ajuster la photo. Molette, + et - : zoom. Glisser : déplacement.
La navigation sélectionne uniquement la photo affichée. Ctrl clic / Maj clic permettent une sélection multiple volontaire.

Clic droit sur la photo : blanc, gris clair, gris moyen, gris foncé, noir et ajustement. La couleur du fond est conservée entre lancements.

Couleurs
Profils ICC intégrés sRGB, Display P3, DCI-P3 et autres profils compatibles LittleCMS. Conversion directe vers le profil Windows du moniteur contenant la fenêtre. Sans profil intégré valide, hypothèse sRGB ; sans profil écran disponible, sortie sRGB. Les anciens aperçus Tk ne sont pas utilisés.

Originaux
Jamais réécrits, renommés, déplacés ou supprimés. Notes, drapeaux et tri sont enregistrés dans TriPhoto_Session/session.json. Export des cinq étoiles visibles avec les filtres actifs vers TriPhoto_Session/Export_5_Etoiles, par copie exacte sans conversion. Sessions existantes conservées.

Formats : JPEG, PNG, TIFF, WebP, BMP.
Dépendances : Python 3, Pillow, PySide6, déjà présents sur ce PC.
Interface actuelle : triphoto_qt.py. Fonctions de session et de couleurs : triphoto.py.
