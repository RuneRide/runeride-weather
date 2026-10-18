// SPDX-License-Identifier: EUPL-1.2
// Fournisseur de champ de vent pour ParticulesVent : lit les MÊMES tuiles PMTiles que le calque couleur
// (dossier `wind10/NN.pmtiles`), donc, si le calque couleur du vent est affiché, aucune requête de plus
// (cache de tuiles partagé via donnees.js). Les en-têtes et métadonnées PMTiles (encodage wind8 ou wind12,
// `max`, zooms) sont lus une fois ; le décodage lui-même est fait dans le shader du champ
// (même règle que couche-gl.js, contrat PoC J1 révision 2).
import { COUCHES, paramsDecodage } from './config.js';
import { archive } from './donnees.js';

/** Résout un temps fractionnaire t (heures depuis le début du tirage) en deux échéances et un poids.
 *  `echeances` : liste triée des échéances disponibles (entiers, heures). Pure : testable.
 *  Hors plage : null. Si l'échéance suivante manque, renvoie un seul fichier (b = null). */
export function resoudreEcheances(t, echeances) {
  if (!echeances.length || !(t >= echeances[0]) || t > echeances[echeances.length - 1]) return null;
  const a = Math.floor(t);
  if (!echeances.includes(a)) return null;
  const b = a + 1;
  const mix = t - a;
  return echeances.includes(b) && mix > 0 ? { a, b, mix } : { a, b: null, mix: 0 };
}

/**
 * Crée un fournisseur de champ adossé à des fichiers PMTiles de vent.
 * @param {object} o
 * @param {(h:number)=>string} o.urlEcheance  URL absolue du fichier de l'échéance h (heures).
 * @param {number[]} o.echeances              échéances disponibles (ex. 0..51).
 * @returns {{ fournisseur:(t:number)=>object|null, pret:Promise<void>, infos:()=>object }}
 *   `fournisseur(t)` renvoie `{ urlA, urlB, mix, zmin, zmax, dec }` (voir ParticulesVent) ou null tant que
 *   les métadonnées ne sont pas lues ou si t est hors plage.
 */
export function fournisseurPmtiles({ urlEcheance, echeances }) {
  const coucheVent = COUCHES.find(c => c.cle === 'vent');
  let cfg = null;
  const pret = (async () => {
    const a = archive(urlEcheance(echeances[0]));
    const [h, meta] = await Promise.all([a.getHeader(), a.getMetadata().catch(() => null)]);
    cfg = { dec: paramsDecodage(coucheVent, meta), zmin: h.minZoom, zmax: h.maxZoom, encodage: meta && meta.encoding };
  })();
  return {
    pret,
    infos: () => cfg,
    fournisseur(t) {
      if (!cfg) return null;
      const r = resoudreEcheances(t, echeances);
      if (!r) return null;
      return { urlA: urlEcheance(r.a), urlB: r.b === null ? null : urlEcheance(r.b), mix: r.mix, zmin: cfg.zmin, zmax: cfg.zmax, dec: cfg.dec };
    },
  };
}
