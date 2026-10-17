# SPDX-License-Identifier: EUPL-1.2
"""Liste les messages d'un fichier GRIB2 AROME : paramètre, unité, niveau, échéances. Usage : lsgrib.py "fichiers/*.grib2" (nécessite le paquet Python eccodes)."""
import sys, glob, collections
import eccodes as e
for f in sorted(glob.glob(sys.argv[1])):
    seen = collections.OrderedDict(); n=0
    with open(f,'rb') as fh:
        while True:
            try: h = e.codes_grib_new_from_file(fh)
            except Exception: break
            if h is None: break
            n+=1
            g = lambda k: (e.codes_get(h,k) if e.codes_is_defined(h,k) else None)
            key=(g('shortName'), g('name'), g('units'), g('typeOfLevel'), g('stepType'))
            lv=g('level'); d=seen.setdefault(key,[set(),set()]); d[0].add(lv); d[1].add(g('step'))
            e.codes_release(h)
    print("="*90); print(f.split('/')[-1],"| messages lus:",n)
    for (sn,name,u,lt,st),(lvls,stp) in seen.items():
        L=sorted(x for x in lvls if x is not None); Ls=(str(L[:4])+'…'+str(L[-2:])+f' ({len(L)} niveaux)') if len(L)>6 else str(L)
        print(f"  {str(sn):10s} {str(name)[:38]:38s} [{u}] {lt} {Ls} ({st}) échéances {sorted(stp)[:3]}…")
