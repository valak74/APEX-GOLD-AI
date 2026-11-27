"""Edge diagnostics: measure what actually predicts price, before building strategies on it.

Three studies, on research data only (never the reserved final out-of-sample period, see ``holdout``):

1. **Cost burden** per timeframe: round-trip cost (spread + slippage + commission) as a share of one ATR.
   A strategy whose stops are ~1 ATR pays this share of its risk on every trade before anything happens.
2. **Component studies**: for every detected element (sweeps, BOS, CHoCH, FVG, order blocks) the forward move
   in the expected direction, in ATR units, at several horizons, *in excess of the average drift* — overall and
   conditioned by session, regime, volatility and higher-timeframe alignment. Samples are thinned so that two
   events of a test never overlap (no double counting), then tested (two-sided t) and ALL tests of the report
   are corrected together (Benjamini–Hochberg, false discovery rate 10 %). Only corrected results count.
3. **Exit study**: the signals of each setup replayed with several exit rules, net of costs, in R.

Everything is descriptive and causal (detectors are truncation-invariant). Volatility terciles use thresholds
computed on the whole research sample — a labelling convenience, flagged in the report.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import NormalDist
from typing import Any, Protocol

import numpy as np

from apex.analysis.indicators import Candles, atr
from apex.analysis.liquidity import equal_levels, fair_value_gaps, order_blocks, sweeps
from apex.analysis.regime import classify_series
from apex.analysis.structure import analyze_structure
from apex.backtest.signals import Signal, StrategyParams, generate, htf_trend_at
from apex.domain.models import SymbolSpec
from apex.market.sessions import bucket_start

HORIZONS: dict[str, tuple[int, ...]] = {"5m": (3, 6, 12, 24), "15m": (2, 4, 8, 16), "1h": (1, 2, 4, 8)}
MAX_HOLD: dict[str, int] = {"5m": 48, "15m": 32, "1h": 24}
FDR = 0.10
MIN_N = 30  # reported
MIN_FINDING_N = 100  # needed for a result to be retained as a finding (small cells are where noise hides)
REGIME_GROUP = {
    "strong_trend": "tendance",
    "weak_trend": "tendance",
    "range": "range",
    "transition": "transition",
    "breakout": "transition",
}
_nd = NormalDist()


# --------------------------------------------------------------------------- helpers
def resample(c: Candles, spreads: np.ndarray, tf: str) -> tuple[Candles, np.ndarray]:
    """Aggregate to ``tf`` (complete groups only; the last, possibly partial, group is dropped)."""
    groups: dict[datetime, list[int]] = {}
    for i, t in enumerate(c.t):
        groups.setdefault(bucket_start(t, tf), []).append(i)
    starts = sorted(groups)[:-1]
    if not starts:
        return Candles([], np.array([]), np.array([]), np.array([]), np.array([]), tf), np.array([])
    o = np.array([c.o[groups[s][0]] for s in starts])
    h = np.array([c.h[groups[s]].max() for s in starts])
    lo = np.array([c.l[groups[s]].min() for s in starts])
    cl = np.array([c.c[groups[s][-1]] for s in starts])
    sp = np.array([float(np.nanmean(spreads[groups[s]])) for s in starts]) if len(spreads) else np.array([])
    return Candles(starts, o, h, lo, cl, tf), sp


def session_of(t: datetime) -> str:
    h = t.hour
    return "asia" if h < 7 else "london" if h < 13 else "newyork" if h < 21 else "late"


def p_value(x: np.ndarray) -> float:
    """Two-sided p-value of mean ≠ 0 (t statistic, normal approximation — fine for n ≥ 30)."""
    if len(x) < 2:
        return 1.0
    sd = float(np.std(x, ddof=1))
    if sd == 0:
        return 0.0 if float(np.mean(x)) != 0 else 1.0
    t = float(np.mean(x)) / (sd / math.sqrt(len(x)))
    return float(2 * (1 - _nd.cdf(abs(t))))


def benjamini_hochberg(ps: Sequence[float], q: float = FDR) -> list[bool]:
    m = len(ps)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: ps[i])
    k_max = 0
    for rank, i in enumerate(order, start=1):
        if ps[i] <= q * rank / m:
            k_max = rank
    keep = set(order[:k_max])
    return [i in keep for i in range(m)]


def thin(idxs: Sequence[int], gap: int) -> list[int]:
    """Keep events at least ``gap`` bars apart (first wins), so forward windows never overlap."""
    out: list[int] = []
    last = -(10**9)
    for i in sorted(idxs):
        if i - last >= gap:
            out.append(i)
            last = i
    return out


def ci95(x: np.ndarray) -> list[float | None]:
    if len(x) < 2:
        return [None, None]
    half = 1.96 * float(np.std(x, ddof=1)) / math.sqrt(len(x))
    return [round(float(np.mean(x)) - half, 4), round(float(np.mean(x)) + half, 4)]


def halves(x: np.ndarray, ts: Sequence[datetime], mid: datetime) -> list[float | None]:
    """Mean of the first and second half of the research period: a real effect shows up in both."""
    first = np.array([v for v, t in zip(x, ts, strict=True) if t < mid])
    second = np.array([v for v, t in zip(x, ts, strict=True) if t >= mid])
    return [
        round(float(first.mean()), 4) if len(first) >= 10 else None,
        round(float(second.mean()), 4) if len(second) >= 10 else None,
    ]


def stable(h: list[float | None], sign: float) -> bool:
    return all(v is not None and v * sign > 0 for v in h)


BASE_KEYS = ("séance", "régime", "volatilité", "tendance de fond")


class MacroLike(Protocol):
    def labels(self, t: datetime) -> dict[str, str]: ...


@dataclass
class Event:
    idx: int
    direction: str  # bullish | bearish


# --------------------------------------------------------------------------- 1. cost burden
def cost_burden(c: Candles, spreads: np.ndarray, spec: SymbolSpec, slippage: float) -> dict[str, Any]:
    a = atr(c)
    ok = ~np.isnan(a)
    if not ok.any():
        return {"timeframe": c.timeframe, "bars": len(c)}
    mid = float(np.median(c.c))
    comm = 2 * float(spec.commission_for(Decimal(1), Decimal(f"{mid:.2f}"))) / float(spec.contract_size)
    sp = float(np.nanmedian(spreads)) if len(spreads) else 0.3
    rt = sp + 2 * slippage + comm  # price units per ounce, round trip
    med_atr = float(np.median(a[ok]))
    return {
        "timeframe": c.timeframe,
        "bars": len(c),
        "median_atr": round(med_atr, 3),
        "median_spread": round(sp, 3),
        "commission_rt": round(comm, 3),
        "slippage_rt": round(2 * slippage, 3),
        "roundtrip_cost": round(rt, 3),
        "cost_pct_atr": round(rt / med_atr * 100, 2),
    }


# --------------------------------------------------------------------------- 2. component studies
def detect_components(c: Candles) -> tuple[dict[str, list[Event]], Any]:
    st = analyze_structure(c)
    pools = equal_levels(c, st.swings)
    comps: dict[str, list[Event]] = {
        "sweep_pool": [],
        "sweep_swing": [],
        "bos": [],
        "choch": [],
        "fvg": [],
        "order_block": [],
    }
    for sw in sweeps(c, st, pools):
        comps["sweep_pool" if sw.target == "pool" else "sweep_swing"].append(Event(sw.idx, sw.direction))
    for e in st.events:
        comps["bos" if e.kind == "BOS" else "choch"].append(Event(e.idx, e.direction))
    for z in fair_value_gaps(c):
        comps["fvg"].append(Event(z.idx, z.direction))
    for z in order_blocks(c, st):
        comps["order_block"].append(Event(z.idx, z.direction))
    return comps, st


COMPONENT_FR = {
    "sweep_pool": "Balayage d'un pool (égaux)",
    "sweep_swing": "Balayage d'un sommet/creux",
    "bos": "Cassure de structure (BOS)",
    "choch": "Changement de caractère (CHoCH)",
    "fvg": "Création d'un FVG",
    "order_block": "Création d'un order block",
}


def component_tests(
    c: Candles, comps: dict[str, list[Event]], st: Any, macro: MacroLike | None = None
) -> list[dict[str, Any]]:
    n = len(c)
    a = atr(c)
    tf = c.timeframe
    hz = HORIZONS.get(tf, (3, 6, 12, 24))
    regimes = classify_series(c, st)
    rel_vol = np.where(np.isnan(a), np.nan, a / c.c)
    valid = rel_vol[~np.isnan(rel_vol)]
    t1, t2 = np.percentile(valid, [33.3, 66.7]) if len(valid) else (np.nan, np.nan)
    htf_ends, htf_tr = htf_trend_at(c, "4h" if tf != "1h" else "1d")

    def htf_dir(i: int) -> str:
        k = bisect_right(htf_ends, c.t[i]) - 1
        return {"up": "bullish", "down": "bearish"}.get(htf_tr[k], "none") if k >= 0 else "none"

    def cond(i: int, d: str) -> dict[str, str]:
        rv = rel_vol[i]
        vol = "basse" if rv <= t1 else "moyenne" if rv <= t2 else "haute"
        hd = htf_dir(i)
        return {
            "séance": {"asia": "Asie", "london": "Londres", "newyork": "New York", "late": "Fin de séance"}[
                session_of(c.t[i])
            ],
            "régime": REGIME_GROUP.get(regimes[i].label, "indéterminé"),
            "volatilité": vol,
            "tendance de fond": "alignée" if hd == d else "neutre" if hd == "none" else "contraire",
        } | (macro.labels(c.t[i]) if macro is not None else {})

    # forward returns in ATR units and the unconditional drift per horizon (long direction)
    fwd: dict[int, np.ndarray] = {}
    drift: dict[int, float] = {}
    for h in hz:
        r = np.full(n, np.nan)
        if n > h:
            r[: n - h] = (c.c[h:] - c.c[: n - h]) / a[: n - h]
        fwd[h] = r
        drift[h] = float(np.nanmean(r)) if np.isfinite(r).any() else 0.0

    mid_t = c.t[n // 2]
    rows: list[dict[str, Any]] = []
    for name, evs in comps.items():
        evs = [e for e in evs if e.idx < n and not np.isnan(a[e.idx])]
        labels = [(e, cond(e.idx, e.direction)) for e in evs]
        groups: list[tuple[str, str, list[Event]]] = [("tout", "tout", evs)]
        keys = sorted({k for _, lab in labels for k in lab}, key=lambda k: (k not in BASE_KEYS, k))
        for key in keys:
            vals = sorted({lab[key] for _, lab in labels if key in lab})
            for v in vals:
                groups.append((key, v, [e for e, lab in labels if lab.get(key) == v]))
        for key, val, sub in groups:
            for h in hz:
                by_idx = {e.idx: e for e in sub}
                kept = [i for i in thin(list(by_idx), h) if not np.isnan(fwd[h][i])]
                x = np.array(
                    [(fwd[h][i] - drift[h]) * (1 if by_idx[i].direction == "bullish" else -1) for i in kept]
                )
                if len(x) < MIN_N:
                    continue
                rows.append(
                    {
                        "timeframe": tf,
                        "component": name,
                        "condition_key": key,
                        "condition": val,
                        "horizon": h,
                        "n": len(x),
                        "events": len(sub),
                        "mean_excess_atr": round(float(np.mean(x)), 4),
                        "ci": ci95(x),
                        "hit_rate": round(float((x > 0).mean()), 4),
                        "p": round(p_value(x), 6),
                        "halves": halves(x, [c.t[i] for i in kept], mid_t),
                    }
                )
    return rows


# --------------------------------------------------------------------------- 3. exit study
SETUP_FR = {"liquidity_sweep_reversal": "balayage de liquidité", "trend_pullback": "repli dans la tendance"}

EXIT_RULES: dict[str, str] = {
    "liquidity": "Objectif de liquidité (règle actuelle)",
    "fixed_1r": "Objectif fixe 1 R",
    "fixed_1_5r": "Objectif fixe 1,5 R",
    "fixed_2r": "Objectif fixe 2 R",
    "fixed_3r": "Objectif fixe 3 R",
    "be_2r": "2 R, stop à l'entrée après +1 R",
    "partial_1r_2r": "Moitié à 1 R, reste à 2 R (stop à l'entrée)",
    "trail_atr": "Stop suiveur 1,5 ATR après +1 R",
    "time": "Sans objectif, sortie au temps maximum",
}


def simulate_exit(
    c: Candles,
    spreads: np.ndarray,
    a: np.ndarray,
    s: Signal,
    rule: str,
    max_hold: int,
    slippage: float,
    comm_rt: float,
) -> float | None:
    """One signal replayed with one exit rule; result in R, net of spread, slippage and commission.

    Same conservative conventions as the backtester: next-open entry, stop first inside a bar, gaps fill
    at the open, stop/target adjustments take effect from the next bar.
    """
    n = len(c)
    j = s.idx + 1
    if j >= n:
        return None
    sgn = 1 if s.direction == "bullish" else -1

    def half(k: int) -> float:
        v = float(spreads[k]) if k < len(spreads) and not np.isnan(spreads[k]) else 0.3
        return max(v, 0.01) / 2

    entry = float(c.o[j]) + sgn * (half(j) + slippage)
    risk = (entry - s.stop) * sgn
    if risk <= 0:
        return None
    unit_atr = float(a[s.idx]) if not np.isnan(a[s.idx]) else risk
    target: float | None
    if rule == "liquidity":
        target = s.target
    elif rule.startswith("fixed_"):
        k_r = {"fixed_1r": 1.0, "fixed_1_5r": 1.5, "fixed_2r": 2.0, "fixed_3r": 3.0}[rule]
        target = entry + sgn * k_r * risk
    elif rule in ("be_2r", "partial_1r_2r"):
        target = entry + sgn * 2 * risk
    else:
        target = None
    stop = s.stop
    armed = False  # +1R reached (break-even / trailing / partial)
    banked = 0.0  # R already realised by a partial exit
    size = 1.0
    best = entry
    for k in range(j, min(n, j + max_hold)):
        cs = -sgn * half(k)  # closing side
        o, hi, lo = float(c.o[k]) + cs, float(c.h[k]) + cs, float(c.l[k]) + cs
        if k > j and (o - stop) * sgn <= 0:
            px = o - sgn * slippage
            return banked + size * (px - entry) * sgn / risk - comm_rt / risk
        adverse = lo if sgn > 0 else hi
        favour = hi if sgn > 0 else lo
        if (adverse - stop) * sgn <= 0:
            px = stop - sgn * slippage
            return banked + size * (px - entry) * sgn / risk - comm_rt / risk
        if rule == "partial_1r_2r" and not armed and (favour - entry) * sgn >= risk:
            banked += 0.5  # half closed at +1R (limit order: no slippage)
            size = 0.5
        if target is not None and (favour - target) * sgn >= 0:
            return banked + size * (target - entry) * sgn / risk - comm_rt / risk
        # adjustments effective from the next bar
        best = max(best, favour) if sgn > 0 else min(best, favour)
        if not armed and (best - entry) * sgn >= risk:
            armed = True
            if rule in ("be_2r", "partial_1r_2r"):
                stop = entry
        if armed and rule == "trail_atr":
            trail = best - sgn * 1.5 * unit_atr
            if (trail - stop) * sgn > 0:
                stop = trail
    k = min(n, j + max_hold) - 1
    px = float(c.c[k]) - sgn * (half(k) + slippage)
    return banked + size * (px - entry) * sgn / risk - comm_rt / risk


def exit_tests(
    c: Candles, spreads: np.ndarray, spec: SymbolSpec, slippage: float, setups: Sequence[str]
) -> list[dict[str, Any]]:
    a = atr(c)
    mid = float(np.median(c.c)) if len(c) else 0.0
    comm_rt = 2 * float(spec.commission_for(Decimal(1), Decimal(f"{mid:.2f}"))) / float(spec.contract_size)
    max_hold = MAX_HOLD.get(c.timeframe, 48)
    rows: list[dict[str, Any]] = []
    for setup in setups:
        sigs = generate(c, StrategyParams(setup=setup, max_hold_bars=max_hold))  # type: ignore[arg-type]
        # one position at a time is not modelled here: signals are thinned to non-overlapping windows
        kept = {i for i in thin([s.idx for s in sigs], max_hold)}
        sigs = [s for s in sigs if s.idx in kept]
        for rule in EXIT_RULES:
            pairs = [(s.t, simulate_exit(c, spreads, a, s, rule, max_hold, slippage, comm_rt)) for s in sigs]
            pairs_ok = [(t, r) for t, r in pairs if r is not None]
            x = np.array([r for _, r in pairs_ok])
            if len(x) < MIN_N:
                rows.append({"timeframe": c.timeframe, "setup": setup, "rule": rule, "n": len(x)})
                continue
            rows.append(
                {
                    "timeframe": c.timeframe,
                    "setup": setup,
                    "rule": rule,
                    "n": len(x),
                    "expectancy_r": round(float(np.mean(x)), 4),
                    "ci": ci95(x),
                    "win_rate": round(float((x > 0).mean()), 4),
                    "p": round(p_value(x), 6),
                    "halves": halves(x, [t for t, _ in pairs_ok], c.t[len(c) // 2]),
                }
            )
    return rows


# --------------------------------------------------------------------------- report
def _fmt(v: float, d: int = 2) -> str:
    return f"{v:+.{d}f}".replace(".", ",")


def findings(
    costs: list[dict[str, Any]], comps: list[dict[str, Any]], exits: list[dict[str, Any]]
) -> list[str]:
    out: list[str] = []
    cb = [x for x in costs if "cost_pct_atr" in x]
    if cb:
        best = min(cb, key=lambda x: x["cost_pct_atr"])
        worst = max(cb, key=lambda x: x["cost_pct_atr"])
        w, b = (
            f"{worst['cost_pct_atr']:.1f}".replace(".", ","),
            f"{best['cost_pct_atr']:.1f}".replace(".", ","),
        )
        span = (
            f"{w} % d'un ATR en {worst['timeframe']}"
            if worst is best
            else f"{w} % d'un ATR en {worst['timeframe']} contre {b} % en {best['timeframe']}"
        )
        out.append(
            f"Coûts : un aller-retour coûte {span}. Un stop d'un ATR perd cette part du risque dès l'entrée."
        )
    sig = [r for r in comps if r["retained"] and r["mean_excess_atr"] > 0]
    neg = [r for r in comps if r["retained"] and r["mean_excess_atr"] < 0]
    if sig:
        best_per: dict[tuple[str, str], dict[str, Any]] = {}
        for r in sorted(sig, key=lambda x: (x["condition_key"] != "tout", -x["mean_excess_atr"])):
            best_per.setdefault(
                (r["timeframe"], r["component"]), r
            )  # overall result first, then the best cell
        for r in list(best_per.values())[:6]:
            cond = "" if r["condition_key"] == "tout" else f" ({r['condition_key']} : {r['condition']})"
            out.append(
                f"Signal exploitable : {COMPONENT_FR[r['component']]}{cond} en {r['timeframe']}, "
                f"{_fmt(r['mean_excess_atr'])} ATR après {r['horizon']} bougies (n = {r['n']}), "
                "significatif après correction, supérieur aux coûts et présent sur les deux moitiés de l'historique."
            )
    else:
        out.append(
            "Aucun élément ne prédit un mouvement à la fois significatif (après correction pour tests multiples) "
            "plus grand que les coûts et stable sur les deux moitiés de l'historique."
        )
    if neg:
        r = sorted(neg, key=lambda x: x["mean_excess_atr"])[0]
        out.append(
            f"À l'inverse : {COMPONENT_FR[r['component']]} en {r['timeframe']} est suivi d'un mouvement CONTRAIRE "
            f"au sens attendu ({_fmt(r['mean_excess_atr'])} ATR, n = {r['n']}). Ne pas inverser le signal sans "
            "le confirmer sur d'autres données."
        )
    good_exits = [r for r in exits if r.get("retained") and r["expectancy_r"] > 0]
    if good_exits:
        r = max(good_exits, key=lambda x: x["expectancy_r"])
        out.append(
            f"Meilleure sortie significative : {EXIT_RULES[r['rule']]} sur « {SETUP_FR.get(r['setup'], r['setup'])} » en {r['timeframe']}, "
            f"{_fmt(r['expectancy_r'], 3)} R par trade (n = {r['n']})."
        )
    else:
        out.append(
            "Aucune combinaison stratégie × sortie n'a une espérance positive significative après coûts."
        )
    return out


def diagnose(
    base: Candles,
    spreads: np.ndarray,
    spec: SymbolSpec,
    timeframes: Sequence[str] = ("5m", "15m", "1h"),
    slippage: float = 0.05,
    setups: Sequence[str] = ("liquidity_sweep_reversal", "trend_pullback"),
    progress: Callable[[str], None] | None = None,
    macro: MacroLike | None = None,
) -> dict[str, Any]:
    costs: list[dict[str, Any]] = []
    comps: list[dict[str, Any]] = []
    exits: list[dict[str, Any]] = []
    counts: dict[str, dict[str, int]] = {}
    for tf in timeframes:
        c, sp = (base, spreads) if tf == base.timeframe else resample(base, spreads, tf)
        if len(c) < 300:
            costs.append({"timeframe": tf, "bars": len(c), "skipped": "moins de 300 bougies"})
            continue
        if progress:
            progress(tf)
        costs.append(cost_burden(c, sp, spec, slippage))
        found, st = detect_components(c)
        counts[tf] = {k: len(v) for k, v in found.items()}
        comps += component_tests(c, found, st, macro)
        exits += exit_tests(c, sp, spec, slippage, setups)
    cost_atr = {x["timeframe"]: x["roundtrip_cost"] / x["median_atr"] for x in costs if "median_atr" in x}
    tested = [r for r in exits if "p" in r]
    # two families, corrected separately: the exit tests are often "significant" simply because costs make
    # them negative, and mixing them in would make the correction more lenient for the components
    for family in (comps, tested):
        for r, f in zip(family, benjamini_hochberg([r["p"] for r in family]), strict=True):
            r["significant"] = f
    for r in comps:
        r["exceeds_cost"] = abs(r["mean_excess_atr"]) > cost_atr.get(r["timeframe"], 0.0)
        r["stable"] = stable(r["halves"], r["mean_excess_atr"])
        r["retained"] = r["significant"] and r["stable"] and r["exceeds_cost"] and r["n"] >= MIN_FINDING_N
    for r in tested:
        r["stable"] = stable(r["halves"], r["expectancy_r"])
        r["retained"] = r["significant"] and r["stable"] and r["n"] >= MIN_FINDING_N
    flags = [r["significant"] for r in comps + tested]
    return {
        "costs": costs,
        "event_counts": counts,
        "components": comps,
        "exits": exits,
        "tests_total": len(flags),
        "significant_total": sum(flags),
        "retained_total": sum(1 for r in comps + tested if r["retained"]),
        "edges_total": sum(1 for r in comps if r["retained"] and r["mean_excess_atr"] > 0)
        + sum(1 for r in tested if r["retained"] and r["expectancy_r"] > 0),
        "fdr": FDR,
        "findings": findings(costs, comps, exits),
        "labels": {"components": COMPONENT_FR, "exits": EXIT_RULES},
        "method": {
            "excess": "mouvement dans le sens attendu moins la dérive moyenne, en ATR",
            "thinning": "événements espacés d'au moins l'horizon (pas de double comptage)",
            "correction": f"Benjamini–Hochberg, FDR {int(FDR * 100)} %, séparément sur les {len(comps)} tests d'éléments et les {len(tested)} tests de sorties",
            "min_sample": f"un résultat n'est retenu qu'avec au moins {MIN_FINDING_N} événements indépendants",
            "stability": "un résultat ne compte que s'il a le même signe sur les deux moitiés de la période de recherche",
            "macro": "conditions macro (taux réels, dollar, COT, jour d'annonce) évaluées avec les seules données publiées à l'instant de chaque événement",
            "volatility_terciles": "seuils calculés sur tout l'échantillon de recherche (étiquetage descriptif)",
            "exits": "entrée à l'ouverture suivante, stop prioritaire, coûts inclus, sans moteur de risque",
        },
    }
