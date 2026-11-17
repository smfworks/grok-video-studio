#!/usr/bin/env python3
"""numbers_extract.py - owner's sheet -> cells.json -> confirm table (Numbers on Screen, 0 generations).
  grid  SHEET.csv|SHEET.xlsx [--sheet NAME] [--low B4,D7] > cells.json   every non-empty cell {ref: {v, conf}}
  draft cells.json > numbers.json        first-guess breakdown (label col + last numeric col; total/price rows)
  confirm numbers.json cells.json > confirm.md   the table the owner checks; exit 1 if any check fails
Photos: read the photo yourself, type it cell-for-cell into transcribed.csv (same rows/columns as the sheet),
list every cell you are not sure of with --low, and continue from `grid`. Low cells are flagged until the owner confirms."""
import csv, json, re, sys
def col(n):
    s = ''
    while n: n, r = divmod(n - 1, 26); s = chr(65 + r) + s
    return s
def num(v):
    if isinstance(v, (int, float)): return float(v)
    t = str(v).strip().replace(',', '').replace('$', '').replace('£', '').replace('€', '')
    pct = t.endswith('%'); t = t.rstrip('%')
    try: return float(t) / (100 if pct else 1)
    except ValueError: return None
def grid(path, sheet=None, low=()):
    if path.lower().endswith(('.xlsx', '.xlsm')):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True); ws = wb[sheet] if sheet else wb.active
        rows = [[c for c in r] for r in ws.iter_rows(values_only=True)]
    else:
        with open(path, newline='', encoding='utf-8-sig') as fh: rows = list(csv.reader(fh))
    out = {}
    for i, r in enumerate(rows, 1):
        for j, v in enumerate(r, 1):
            if v is None or str(v).strip() == '': continue
            ref = f'{col(j)}{i}'; n = num(v)
            out[ref] = {'v': n if n is not None else str(v).strip(), 'conf': 'low' if ref in low else 'high'}
    return out
def split(ref): m = re.match(r'([A-Z]+)(\d+)$', ref); return m.group(1), int(m.group(2))
def draft(cells):
    rows = {}
    for ref, c in cells.items(): cl, r = split(ref); rows.setdefault(r, {})[cl] = (ref, c['v'])
    items, total, price, volume = [], None, None, None
    for r in sorted(rows):
        txt = [v for _, v in rows[r].values() if isinstance(v, str)]
        nums = [(ref, v) for ref, v in rows[r].values() if isinstance(v, float)]
        if not txt or not nums: continue
        lab = txt[0]; ref, v = sorted(nums, key=lambda x: (len(split(x[0])[0]), split(x[0])[0]))[-1]
        lo = lab.lower()
        if 'total' in lo: total = {'value': v, 'cell': ref}
        elif 'price' in lo: price = {'value': v, 'cell': ref, 'label': lab}
        elif 'week' in lo or 'sold' in lo: volume = {'value': v, 'cell': ref, 'label': lab}
        else: items.append({'label': lab, 'value': v, 'cell': ref})
    sc = {'type': 'breakdown', 'title': 'Cost breakdown', 'items': items, 'total': total}
    if price: sc['price'] = price
    out = {'title': 'TITLE', 'currency': '$', 'example': False, 'source': 'SHEET', 'hook': 'HOOK', 'scenes': [sc]}
    if price and volume: out['scenes'].append({'type': 'annualize', 'title': 'A 2-point drop', 'price': price, 'points': 2, 'volume': volume, 'periods': 52, 'period_label': 'weeks'})
    return out
def refs(o, lab=''):
    if isinstance(o, dict):
        lab = o.get('label', lab)
        if 'cell' in o and 'value' in o: yield o, lab
        for side in ('before', 'after'):
            if side + '_cells' in o:
                for v, c in zip(o[side], o[side + '_cells']): yield {'value': v, 'cell': c}, f'{lab} ({side})'
        for k, v in o.items():
            if isinstance(v, (dict, list)): yield from refs(v, f'{lab} ({k.replace("_", " ")})' if k in ('qty', 'unit_cost') else lab)
    elif isinstance(o, list):
        for v in o: yield from refs(v, lab)
def confirm(nj, cells):
    sys.path.insert(0, __file__.rsplit('/', 1)[0]); import numbers_kit as nk
    L = [f"# Confirm these figures ({nj.get('source', '')})", '', '| Cell | Label | Value on sheet | Used in video | Check |', '|---|---|---|---|---|']
    bad, seen = 0, set()
    for o, lab in refs(nj):
        if o['cell'] in seen: continue
        seen.add(o['cell'])
        c = cells.get(o['cell']); sv = c['v'] if c else None
        ok = isinstance(sv, float) and abs(sv - o['value']) < 1e-6
        flag = 'OK' if ok else 'MISMATCH'
        if c and c['conf'] == 'low' and not c.get('owner_checked'): flag = 'CHECK (hard to read)'
        bad += flag != 'OK'
        L.append(f"| {o['cell']} | {lab} | {sv if sv is not None else 'missing'} | {o['value']} | {flag} |")
    L += ['', '## Recomputed'] + [f'- {m}' for m in nk.check(nj)]
    L += ['', 'Example figures label: ' + ('ON (made-up or sample numbers)' if nj.get('example') else 'off (real figures from the owner)')]
    return '\n'.join(L), bad
if __name__ == '__main__':
    a = sys.argv[1:]
    if a[0] == 'grid':
        low = a[a.index('--low') + 1].split(',') if '--low' in a else ()
        sh = a[a.index('--sheet') + 1] if '--sheet' in a else None
        print(json.dumps(grid(a[1], sh, low), indent=1))
    elif a[0] == 'draft': print(json.dumps(draft(json.load(open(a[1]))), indent=1))
    elif a[0] == 'confirm':
        try: txt, bad = confirm(json.load(open(a[1])), json.load(open(a[2])))
        except AssertionError as e: print('CHECK FAILED:', e); sys.exit(1)
        print(txt); sys.exit(1 if bad else 0)
