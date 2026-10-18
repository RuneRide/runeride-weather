// SPDX-License-Identifier: EUPL-1.2
// Textes de l'interface en français (langue d'origine) et en anglais, choisis d'après la langue du navigateur.
// Module PUR (aucun accès au DOM ni à `navigator` : les valeurs sont passées en paramètres, voir les tests).
// Périmètre : l'habillage de l'interface (boutons, couches, légende, erreurs, « À propos »). Les libellés de
// tirage, d'échéance et de date (pastille, frise, avis de repli : format-temps.js, integration-logique.js) restent
// en français : traduction non faite (voir docs/mise-en-production.md, limites).

export const LANGUES = ['fr', 'en'];

/** Langue d'affichage : `?lang=fr|en` d'abord, sinon la première langue du navigateur reconnue, sinon le français. */
export function detecterLangue(langues = [], forcee = null) {
  const f = String(forcee || '').toLowerCase().slice(0, 2);
  if (LANGUES.includes(f)) return f;
  for (const l of langues || []) {
    const c = String(l || '').toLowerCase().slice(0, 2);
    if (LANGUES.includes(c)) return c;
  }
  return 'fr';
}

export const TEXTES = {
  fr: {
    'titre': 'Les Augures · RuneRide Météo',
    'description': "Prévisions météo AROME de Météo-France sur la France et ses abords : vent, température, pluie, nuages, avec animation heure par heure. Gratuit, sans compte.",
    'carte': 'Carte météo',
    'couches': 'Couche affichée', 'modeles': 'Modèle et superpositions', 'modele': 'Modèle',
    'auto': 'Auto', 'auto.titre': 'Choix automatique selon le zoom',
    'particules': 'Particules', 'isobares': 'Isobares',
    'pastille.titre': 'Modèle et tirage affichés : toucher pour le détail', 'tirage.detail': 'Détail du tirage',
    'info.fermer': 'Effacer le point', 'legende': 'Légende',
    'lecture': 'Lecture', 'pause': 'Pause', 'lecture.titre': 'Lecture / pause (espace)',
    'vitesse': "Vitesse d'animation", 'vitesse.titre': 'Vitesse', 'echeance': 'Échéance',
    'info.chargement': 'chargement', 'info.vide': 'pas de donnée',
    'pression.titre': 'Pression au niveau de la mer (hPa)', 'pression.legende': 'Isobares tous les 2 hPa · A anticyclone · D dépression',
    'particules.reduit': 'Mouvement réduit demandé : flèches à la place des particules', 'particules.titre': 'Particules de vent animées',
    'couche.vent': 'Vent', 'couche.rafales': 'Rafales', 'couche.t2m': 'Température', 'couche.pluie': 'Pluie', 'couche.nuages': 'Nuages',
    'couche.humidite': 'Humidité', 'couche.neige': 'Neige (part)', 'couche.cape': 'Orages (CAPE)', 'couche.pression': 'Pression',
    'unite.neige': '% neige',
    'source': 'Source', 'licence': 'Licence Ouverte v2.0', 'apropos': 'À propos et sources',
    'apropos.fermer': 'Fermer', 'apropos.titre': 'À propos et sources',
    'chargement': 'Chargement des prévisions…',
    'err.titre': 'Prévisions indisponibles',
    'err.horsligne': "Vous semblez être hors ligne. La carte se rechargera dès le retour du réseau.",
    'err.manifeste': "Impossible de joindre le serveur de prévisions. Réessayez dans un instant.",
    'err.vide': "Aucun tirage de prévision n'est publié pour le moment. Réessayez dans quelques minutes.",
    'err.invalide': 'La liste des prévisions reçue est illisible. Réessayez dans un instant.',
    'err.webgl': "Votre navigateur ne permet pas l'affichage de la carte (WebGL indisponible).",
    'err.inattendue': 'Une erreur inattendue est survenue. Rechargez la page.',
    'err.reessayer': 'Réessayer',
    'statut.horsligne': 'Hors ligne : seules les données déjà chargées sont affichées.',
    'statut.retour': 'Connexion rétablie.',
    'statut.tuiles': "Certaines données n'ont pas pu être chargées ; nouvel essai en cours.",
    'statut.ancien': 'Les prévisions affichées datent de plus de {h} h : le tirage suivant tarde à être publié.',
    'statut.fond': 'Une partie de la carte ne s’est pas chargée.',
  },
  en: {
    'titre': 'Les Augures · RuneRide Weather',
    'description': 'Météo-France AROME weather forecasts for France and its surroundings: wind, temperature, rain, cloud, with hour-by-hour animation. Free, no account.',
    'carte': 'Weather map',
    'couches': 'Displayed layer', 'modeles': 'Model and overlays', 'modele': 'Model',
    'auto': 'Auto', 'auto.titre': 'Automatic choice according to zoom',
    'particules': 'Particles', 'isobares': 'Isobars',
    'pastille.titre': 'Model and forecast run shown: tap for details', 'tirage.detail': 'Forecast run details',
    'info.fermer': 'Clear the point', 'legende': 'Legend',
    'lecture': 'Play', 'pause': 'Pause', 'lecture.titre': 'Play / pause (space)',
    'vitesse': 'Animation speed', 'vitesse.titre': 'Speed', 'echeance': 'Forecast time',
    'info.chargement': 'loading', 'info.vide': 'no data',
    'pression.titre': 'Mean sea-level pressure (hPa)', 'pression.legende': 'Isobars every 2 hPa · A high · D low',
    'particules.reduit': 'Reduced motion requested: arrows instead of particles', 'particules.titre': 'Animated wind particles',
    'couche.vent': 'Wind', 'couche.rafales': 'Gusts', 'couche.t2m': 'Temperature', 'couche.pluie': 'Rain', 'couche.nuages': 'Cloud',
    'couche.humidite': 'Humidity', 'couche.neige': 'Snow (share)', 'couche.cape': 'Thunderstorms (CAPE)', 'couche.pression': 'Pressure',
    'unite.neige': '% snow',
    'source': 'Source', 'licence': 'Open Licence v2.0', 'apropos': 'About and sources',
    'apropos.fermer': 'Close', 'apropos.titre': 'About and sources',
    'chargement': 'Loading forecasts…',
    'err.titre': 'Forecasts unavailable',
    'err.horsligne': 'You appear to be offline. The map will reload as soon as the network is back.',
    'err.manifeste': 'The forecast server cannot be reached. Please try again in a moment.',
    'err.vide': 'No forecast run is published at the moment. Please try again in a few minutes.',
    'err.invalide': 'The forecast list received cannot be read. Please try again in a moment.',
    'err.webgl': 'Your browser cannot display the map (WebGL unavailable).',
    'err.inattendue': 'An unexpected error occurred. Please reload the page.',
    'err.reessayer': 'Try again',
    'statut.horsligne': 'Offline: only data already loaded is shown.',
    'statut.retour': 'Connection restored.',
    'statut.tuiles': 'Some data could not be loaded; retrying.',
    'statut.ancien': 'The forecasts shown are more than {h} h old: the next run is late.',
    'statut.fond': 'Part of the map did not load.',
  },
};

/** Fabrique la fonction de traduction `t(cle, vars)` ; repli sur le français puis sur la clé. `{nom}` est remplacé par vars.nom. */
export function creerT(langue) {
  const table = TEXTES[langue] || TEXTES.fr;
  return (cle, vars) => {
    let s = table[cle] ?? TEXTES.fr[cle] ?? cle;
    if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
    return s;
  };
}
