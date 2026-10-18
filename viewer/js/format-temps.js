// SPDX-License-Identifier: EUPL-1.2
// Formatage PUR des dates et heures du viewer : tirage complet (UTC et heure locale), échéance, fraîcheur, libellé de frise.
// Aucune horloge ni DOM : l'instant « maintenant » est passé en paramètre (tests à dates fixes).
// Fuseau local par défaut : Europe/Paris (heure d'été / d'hiver gérées par Intl ; jamais d'heure locale calculée à la main).

export const FUSEAU = 'Europe/Paris';
const H = 3600e3;

/** Nom court de la ville d'un fuseau IANA (« Europe/Paris » → « Paris »). */
export const nomVille = tz => String(tz).split('/').pop().replace(/_/g, ' ');

const cache = new Map();
function fmt(tz, opt) {
  const cle = tz + JSON.stringify(opt);
  if (!cache.has(cle)) cache.set(cle, new Intl.DateTimeFormat('fr-FR', { timeZone: tz, ...opt }));
  return cache.get(cle);
}
const pieces = (ms, tz, opt) => Object.fromEntries(fmt(tz, opt).formatToParts(new Date(ms)).map(p => [p.type, p.value]));
const pad = n => String(n).padStart(2, '0');

/** Heure d'horloge : « 05 h » ou « 05 h 30 » dans le fuseau `tz` (UTC compris). */
export function heureCourte(ms, tz = FUSEAU) {
  const p = pieces(ms, tz, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  return p.minute === '00' ? `${p.hour} h` : `${p.hour} h ${p.minute}`;
}
/** Jour court : « sam. 17 oct. ». */
export function jourCourt(ms, tz = FUSEAU) {
  const p = pieces(ms, tz, { weekday: 'short', day: 'numeric', month: 'short' });
  return `${p.weekday} ${p.day} ${p.month}`;
}
/** Date numérique : « 17/10 ». */
export function dateNumerique(ms, tz = FUSEAU) {
  const p = pieces(ms, tz, { day: '2-digit', month: '2-digit' });
  return `${p.day}/${p.month}`;
}
/** Décalage du fuseau à cet instant : « UTC+2 ». */
export function decalage(ms, tz = FUSEAU) {
  return pieces(ms, tz, { timeZoneName: 'short' }).timeZoneName;
}
/** Vrai si la même heure d'horloge locale se produit deux fois à moins d'une heure d'écart (nuit du passage à l'heure d'hiver). */
export function heureAmbigue(ms, tz = FUSEAU) {
  const h = heureCourte(ms, tz);
  return heureCourte(ms - H, tz) === h || heureCourte(ms + H, tz) === h;
}
/** Heure locale avec le nom de la ville ; le décalage est ajouté si l'heure est ambiguë : « 02 h Paris (UTC+1) ». */
export function heureLocale(ms, tz = FUSEAU) {
  const base = `${heureCourte(ms, tz)} ${nomVille(tz)}`;
  return heureAmbigue(ms, tz) ? `${base} (${decalage(ms, tz)})` : base;
}

/** Âge relatif en français : « à l'instant », « il y a 35 min », « il y a 21 h », « il y a 3 j ». */
export function ageRelatif(ms, maintenant) {
  const d = maintenant - ms;
  if (!(d >= 60e3)) return "à l'instant";            // aussi pour une horloge en avance (futur)
  if (d < 3600e3) return `il y a ${Math.round(d / 60e3)} min`;
  if (d < 48 * H) return `il y a ${Math.round(d / H)} h`;
  return `il y a ${Math.round(d / (24 * H))} j`;
}
/** « traité le 17/10 à 13 h 40 UTC (il y a 21 h) » ; date de TRAITEMENT chez nous, pas de publication Météo-France. */
export function formatTraite(iso, maintenant, tz = FUSEAU) {
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return '';
  return `traité le ${dateNumerique(ms, 'UTC')} à ${heureCourte(ms, 'UTC')} UTC (${ageRelatif(ms, maintenant)})`;
}
/** Échéance affichée : « +35 h » (entière) ou « +35,5 h ». */
export function formatEcheance(runMs, tValide) {
  const h = (tValide - runMs) / H;
  const r = Math.round(h * 10) / 10;
  return `+${String(Number.isInteger(r) ? r : r.toFixed(1)).replace('.', ',')} h`;
}
/** Nom court d'un modèle pour la forme compacte. */
export const modeleCourt = m => (m === 'arome-hd' ? 'HD' : m === 'arome' ? 'AROME' : String(m));

/**
 * Description complète d'un tirage pour un temps valide affiché.
 * @returns {{ longue, compacte, tirage, local, echeance }}
 *  longue   « AROME HD 0,01° · tirage du sam. 17 oct. à 03 h UTC (05 h Paris) · +35 h »
 *  compacte « HD · 17/10 03 h UTC · +35 h »
 */
export function decrireTirage({ run, tValide, modele, libelle, tz = FUSEAU }) {
  const r = Date.parse(run);
  const echeance = formatEcheance(r, tValide);
  const tirage = `tirage du ${jourCourt(r, 'UTC')} à ${heureCourte(r, 'UTC')} UTC`;
  const local = `(${heureLocale(r, tz)})`;
  return {
    longue: `${libelle} · ${tirage} ${local} · ${echeance}`,
    compacte: `${modeleCourt(modele)} · ${dateNumerique(r, 'UTC')} ${heureCourte(r, 'UTC')} UTC · ${echeance}`,
    tirage: `${tirage[0].toUpperCase()}${tirage.slice(1)} ${local}`, local, echeance,
  };
}

/** Libellé de la frise : « dim. 18 oct. 14 h Paris » + « 12 h UTC » ; le jour est celui de l'heure locale. */
export function etiquetteFrise(tValide, tz = FUSEAU) {
  const utc = `${heureCourte(tValide, 'UTC')} UTC`;
  const local = `${jourCourt(tValide, tz)} ${heureLocale(tValide, tz)}`;
  return { local, utc, complet: `${local} · ${utc}`, infobulle: `${jourCourt(tValide, 'UTC')} ${heureCourte(tValide, 'UTC')} UTC` };
}

/** Ligne pour une couche qui n'utilise pas le même modèle ou tirage que la pastille : « Pression : AROME, tirage du 17/10 à 06 h UTC ». */
export function ligneAutreSource({ nom, libelle, run, tValide, tz = FUSEAU }) {
  const r = Date.parse(run);
  return `${nom} : ${libelle}, tirage du ${dateNumerique(r, 'UTC')} à ${heureCourte(r, 'UTC')} UTC (${heureLocale(r, tz)}), ${formatEcheance(r, tValide)}`;
}
