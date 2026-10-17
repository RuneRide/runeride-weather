// SPDX-License-Identifier: EUPL-1.2
// Surcouche 2D : flèches de vent discrètes (second plan) et repère du point choisi.
// Les valeurs sont lues côté CPU dans les mêmes tuiles que celles dessinées par le shader.
import { lireAuPoint } from './donnees.js';

const PAS = 58; // espacement des flèches, en pixels CSS

export class Surcouche {
  constructor(canvas, map) {
    this.c = canvas; this.map = map; this.ctx = canvas.getContext('2d');
    this.planifie = false;
  }
  redimensionner() {
    const r = this.c.getBoundingClientRect(), d = window.devicePixelRatio || 1;
    this.c.width = Math.round(r.width * d); this.c.height = Math.round(r.height * d);
    this.ctx.setTransform(d, 0, 0, d, 0, 0);
    this.w = r.width; this.h = r.height;
  }
  /** Demande un redessin à la prochaine image (regroupe les appels). */
  planifier(etat) {
    this.etat = etat;
    if (this.planifie) return;
    this.planifie = true;
    requestAnimationFrame(() => { this.planifie = false; this.dessiner(this.etat); });
  }
  dessiner({ couche, urlA, urlB, mix, zoneZ, zmin, point, fleches }) {
    const ctx = this.ctx;
    if (!this.w) this.redimensionner();
    ctx.clearRect(0, 0, this.w, this.h);
    let nb = 0;
    if (fleches && couche && couche.fleches && urlA) {
      ctx.lineWidth = 1.6; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
      for (let y = PAS / 2; y < this.h; y += PAS) {
        for (let x = PAS / 2; x < this.w; x += PAS) {
          const ll = this.map.unproject([x, y]);
          const a = lireAuPoint(urlA, couche, ll.lng, ll.lat, zoneZ, zmin);
          if (!a || !a.valeur) continue;
          let [u, v] = a.valeur;
          if (urlB && mix > 0) {
            const b = lireAuPoint(urlB, couche, ll.lng, ll.lat, zoneZ, zmin);
            if (b && b.valeur) { u += (b.valeur[0] - u) * mix; v += (b.valeur[1] - v) * mix; }
          }
          const vit = Math.hypot(u, v);
          if (vit < 0.5) continue;
          const L = Math.min(30, 10 + vit * 0.9), ang = Math.atan2(-v, u); // v positif = vers le nord = vers le haut
          const dx = Math.cos(ang), dy = Math.sin(ang), x0 = x - dx * L / 2, y0 = y - dy * L / 2, x1 = x + dx * L / 2, y1 = y + dy * L / 2;
          ctx.strokeStyle = 'rgba(235,232,224,0.62)';
          ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
          ctx.moveTo(x1 - dx * 6 - dy * 3.5, y1 - dy * 6 + dx * 3.5); ctx.lineTo(x1, y1); ctx.lineTo(x1 - dx * 6 + dy * 3.5, y1 - dy * 6 - dx * 3.5);
          ctx.stroke(); nb++;
        }
      }
    }
    if (point && point.epingle) {
      const p = this.map.project([point.lng, point.lat]);
      ctx.fillStyle = '#C6934B'; ctx.strokeStyle = '#17191C'; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(p.x, p.y, 7, 0, 7); ctx.fill(); ctx.stroke();
      ctx.strokeStyle = '#EBE8E0'; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(p.x, p.y, 11, 0, 7); ctx.stroke();
    }
    this.nbFleches = nb;
  }
}
