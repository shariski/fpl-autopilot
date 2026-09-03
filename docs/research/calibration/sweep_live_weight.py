"""Sweep live-data window weighting variants (early-season blend calibration).

Variant of the v0.23 blend simulation: prior season as databank, live season
fed GW-by-GW as live rows. Emulates the v0.27 season-aware window defaults by
passing explicit counts/weights to the ratings functions.
"""
import argparse
import functools

from src.analytics import ratings as R
import backtest  # noqa: E402  (PYTHONPATH=docs/research/calibration)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-gw", type=int, default=6)
    ap.add_argument("--only", default=None, help="variant name to run (else all)")
    args = ap.parse_args()

    variants = {
        # name: (lf_count, sf_count, lf_weight, sf_weight)
        "baseline_382_0802": (38, 6, 0.8, 0.2),
        "v027_lf20_0604": (20, 6, 0.6, 0.4),
        "v027_lf20_0505": (20, 6, 0.5, 0.5),
        "v027_lf12_0604": (12, 6, 0.6, 0.4),
        "v027_lf20_0406": (20, 6, 0.4, 0.6),
    }

    real_team = R.compute_team_ratings
    real_player = R.compute_player_rates

    def run(variant):
        lf_c, sf_c, lf_w, sf_w = variants[variant]
        R.compute_team_ratings = functools.partial(
            real_team, lf_gw_count=lf_c, sf_gw_count=sf_c,
            lf_weight=lf_w, sf_weight=sf_w)
        R.compute_player_rates = functools.partial(real_player, lf_gw_count=lf_c, sf_gw_count=sf_c)
        print(f"\n########## {variant}: LF{lf_c}/SF{sf_c} weights {lf_w}/{sf_w} ##########")
        ns = argparse.Namespace(prior="2024-25", live="2025-26", max_gw=args.max_gw)
        return backtest.simulate(ns)

    names = [args.only] if args.only else list(variants)
    for name in names:
        run(name)


if __name__ == "__main__":
    main()
