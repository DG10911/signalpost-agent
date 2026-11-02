"""Combination search with leave-one-day-out check on the real L2 lab."""
import sys, os, dataclasses, itertools, json
from multiprocessing import Pool
sys.path.insert(0, '.'); sys.path.insert(0, 'research')
import lab, realsim
from tuned_config import TUNED
D = os.path.join(os.path.dirname(__file__), '..', '..', '.context', 'lab', 'data')
ms, bs, cl = lab.load_all(os.path.join(D, 'books_flat.parquet'), os.path.join(D, 'btc_1s_oct3_6.bin'))
OFFS = list(range(0, len(ms) - 288 + 1, 24))
GRID = dict(min_edge_per_share_usd=[0.05, 0.055, 0.06], max_hold_seconds=[30, 40, 50],
            entry_window_seconds=[90.0, 120.0], take_profit_bid=[0.95, 0.98],
            thesis_flip_prob=[0.40, 0.45], stop_loss_per_share_usd=[0.35, 0.6])
def run(kw):
    cfg = dataclasses.replace(TUNED, **kw)
    out = []
    for o in OFFS:
        r = realsim.run_window(ms, cl, bs, o, 288, config=cfg)
        out.append((o, r['volume'] >= 1000, r['cash']))
    return kw, out
if __name__ == '__main__':
    keys = list(GRID)
    combos = [dict(zip(keys, v)) for v in itertools.product(*GRID.values())]
    with Pool(7) as p, open(sys.argv[1], 'w') as f:
        for kw, out in p.imap_unordered(run, combos):
            f.write(json.dumps({'kw': kw, 'w': out}) + '\n'); f.flush()
