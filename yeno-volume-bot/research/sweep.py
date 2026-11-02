import sys, os, dataclasses, json, statistics
sys.path.insert(0, '.'); sys.path.insert(0, 'research')
import lab, realsim
from tuned_config import TUNED

BOOKS = os.environ.get('BOOKS', '/Users/devanshgoenka/conductor/workspaces/aiiiii/bishkek/.context/lab/data/books_flat.parquet')
BTC = os.environ.get('BTC', '/Users/devanshgoenka/conductor/workspaces/aiiiii/bishkek/.context/lab/data/btc_1s_oct3_6.bin')
ms, bs, cl = lab.load_all(BOOKS, BTC)

def score(cfg, offs):
    q = 0; cash = []; mdds = []; fees = []; cyc = []
    for off in offs:
        r = realsim.run_window(ms, cl, bs, off, 288, config=cfg)
        if r['volume'] >= 1000:
            q += 1; cash.append(r['cash'])
        mdds.append(r['mdd']); fees.append(r['fees']); cyc.append(r['cycles'])
    return {'q': q, 'n': len(offs), 'min': min(cash) if cash else -1,
            'med': statistics.median(cash) if cash else 0,
            'mdd': statistics.mean(mdds), 'fees': statistics.mean(fees), 'cyc': statistics.mean(cyc)}

if __name__ == '__main__':
    batch = sys.argv[1] if len(sys.argv) > 1 else 'A'
    offs = list(range(0, len(ms) - 288 + 1, 24))
    configs = []
    if batch == 'A':
        for v in (0.90, 0.93, 0.95, 0.97, 0.98, 0.99): configs.append({'take_profit_bid': v})
        for v in (0.35, 0.40, 0.45, 0.50, 0.55): configs.append({'thesis_flip_prob': v})
        for v in (20, 25, 30, 40, 50, 60): configs.append({'max_hold_seconds': v})
    elif batch == 'B':
        for v in (0.040, 0.045, 0.050, 0.055, 0.060, 0.070): configs.append({'min_edge_per_share_usd': v})
        for v in (0.20, 0.25, 0.30, 0.35): configs.append({'min_entry_price': v})
        for v in (0.80, 0.85, 0.90): configs.append({'max_entry_price': v})
        for v in (0.015, 0.020, 0.025, 0.030): configs.append({'max_spread': v})
        for v in (3, 4, 5, 6): configs.append({'max_cycles_per_market': v})
    elif batch == 'C':
        for v in (0.20, 0.25, 0.30, 0.35, 0.40): configs.append({'size_fraction': v, 'low_cash_fraction': v})
        for v in (0.20, 0.35, 0.60, 1.0): configs.append({'stop_loss_per_share_usd': v})
        for v in (0.9, 1.0, 1.1, 1.2): configs.append({'vol_multiplier': v})
        for v in (90.0, 120.0, 150.0): configs.append({'entry_window_seconds': v})
        for v in (5.0, 10.0, 15.0): configs.append({'forced_exit_seconds': v})
        configs.append({'basis_enabled': False})
    base = {'label': 'BASE', **score(TUNED, offs)}
    results = [base]
    for kw in configs:
        cfg = dataclasses.replace(TUNED, **kw)
        s = score(cfg, offs)
        results.append({'label': json.dumps(kw), **s})
        print(f"{s['q']:2d}/{s['n']} min ${s['min']:6.1f} med ${s['med']:6.1f} mdd ${s['mdd']:5.1f} fee ${s['fees']:5.1f} cyc {s['cyc']:5.1f}  {kw}", flush=True)
    print('\nBASE', base)
    print('\nTOP')
    for r in sorted(results, key=lambda x: (x['q'], x['min'], x['med']), reverse=True)[:8]:
        print(r)
