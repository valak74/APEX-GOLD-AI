"use client";

import {
  CandlestickSeries,
  createSeriesMarkers,
  LineStyle,
  ColorType,
  CrosshairMode,
  createChart,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { api, type BarOut, type Overlays } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import { Segmented } from "./ui";

const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"] as const;
type TF = (typeof TIMEFRAMES)[number];

// Up = hollow teal, down = filled orange: validated colour pair + shape cue (never colour alone).
const UP = "#00a888";
const DOWN = "#e76624";

// The library renders timestamps as UTC; shift them so the axis reads in the viewer's local time.
const TZ_SHIFT = -new Date().getTimezoneOffset() * 60;

function toCandle(b: BarOut): CandlestickData<Time> {
  return {
    time: (Math.floor(new Date(b.start).getTime() / 1000) + TZ_SHIFT) as UTCTimestamp,
    open: Number(b.open),
    high: Number(b.high),
    low: Number(b.low),
    close: Number(b.close),
  };
}

const LEVELS: Record<string, { title: string; color: string }> = {
  prev_day_high: { title: "Veille H", color: "#9aa3b5" },
  prev_day_low: { title: "Veille B", color: "#9aa3b5" },
  prev_week_high: { title: "Sem. H", color: "#c9a24f" },
  prev_week_low: { title: "Sem. B", color: "#c9a24f" },
};

function toTime(iso: string): UTCTimestamp {
  return (Math.floor(new Date(iso).getTime() / 1000) + TZ_SHIFT) as UTCTimestamp;
}

type Readout = { o: number; h: number; l: number; c: number; t: string } | null;

export function PriceChart() {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const loadedTf = useRef<string | null>(null);
  const lastTime = useRef<number>(0);
  const [tf, setTf] = useState<TF>("5m");
  const [empty, setEmpty] = useState(true);
  const [readout, setReadout] = useState<Readout>(null);
  const [last, setLast] = useState<Readout>(null);
  const [showLevels, setShowLevels] = useState(true);
  const [showStructure, setShowStructure] = useState(true);
  const [overlays, setOverlays] = useState<Overlays | null>(null);
  const lines = useRef<IPriceLine[]>([]);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);

  useEffect(() => {
    if (!host.current) return;
    const c = createChart(host.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#8b93a7",
        fontFamily: "var(--font-geist-mono), ui-monospace, monospace",
        fontSize: 11,
        attributionLogo: false,
      },
      grid: { vertLines: { color: "#1a1f2a" }, horzLines: { color: "#1a1f2a" } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.12, bottom: 0.08 } },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false, rightOffset: 4 },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "#e0b55b66", labelBackgroundColor: "#1c212c" },
        horzLine: { color: "#e0b55b66", labelBackgroundColor: "#1c212c" },
      },
      handleScale: { axisPressedMouseMove: true },
      localization: { locale: "fr-FR", dateFormat: "dd/MM/yy" },
    });
    const s = c.addSeries(CandlestickSeries, {
      upColor: "rgba(0,0,0,0)",
      borderUpColor: UP,
      wickUpColor: UP,
      downColor: DOWN,
      borderDownColor: DOWN,
      wickDownColor: DOWN,
      priceLineColor: "#e0b55b",
      priceLineStyle: 2,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });
    c.subscribeCrosshairMove((p) => {
      const d = p.seriesData.get(s) as CandlestickData<Time> | undefined;
      if (!d || p.time === undefined) return setReadout(null);
      const t = new Date((Number(p.time) - TZ_SHIFT) * 1000);
      setReadout({ o: d.open, h: d.high, l: d.low, c: d.close, t: t.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) });
    });
    chart.current = c;
    series.current = s;
    markers.current = createSeriesMarkers(s, []);
    return () => {
      markers.current = null;
      lines.current = [];
      c.remove();
      chart.current = null;
      series.current = null;
    };
  }, []);

  usePoll(
    async () => {
      const s = series.current;
      if (!s) return;
      let bars: BarOut[];
      try {
        bars = await api<BarOut[]>(`/market/bars?timeframe=${tf}&limit=400`);
      } catch {
        return;
      }
      setEmpty(bars.length === 0);
      if (!bars.length) return;
      const candles = bars.map(toCandle);
      const lt = Number(candles[candles.length - 1].time);
      const fresh = candles.filter((x) => Number(x.time) >= lastTime.current);
      if (loadedTf.current !== tf || fresh.length > 3) {
        s.setData(candles); // first load, timeframe change, or catching up after a pause
        if (loadedTf.current !== tf) chart.current?.timeScale().scrollToRealTime();
        loadedTf.current = tf;
      } else {
        for (const x of fresh) s.update(x); // update() only accepts the last bar or newer
      }
      lastTime.current = lt;
      const b = candles[candles.length - 1];
      setLast({ o: b.open, h: b.high, l: b.low, c: b.close, t: "" });
    },
    2000,
    [tf],
  );

  usePoll(
    async () => {
      setOverlays(await api<Overlays>(`/analysis/overlays?timeframe=${tf}`).catch(() => null));
    },
    15000,
    [tf],
  );

  useEffect(() => {
    const s = series.current;
    if (!s) return;
    for (const l of lines.current) s.removePriceLine(l);
    lines.current = [];
    if (showLevels && overlays) {
      for (const lv of overlays.levels) {
        const meta = LEVELS[lv.name];
        if (!meta) continue;
        lines.current.push(
          s.createPriceLine({ price: lv.price, color: meta.color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: meta.title }),
        );
      }
    }
    const m: SeriesMarker<Time>[] =
      showStructure && overlays
        ? overlays.events
            .map((e) => ({
              time: toTime(e.t),
              position: e.direction === "bullish" ? ("belowBar" as const) : ("aboveBar" as const),
              color: e.direction === "bullish" ? UP : DOWN,
              shape: e.direction === "bullish" ? ("arrowUp" as const) : ("arrowDown" as const),
              text: e.kind,
            }))
            .sort((a, b) => Number(a.time) - Number(b.time))
        : [];
    markers.current?.setMarkers(m);
  }, [overlays, showLevels, showStructure]);

  const r = readout ?? last;
  const chg = r ? r.c - r.o : 0;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 pt-4">
        <div className="flex items-baseline gap-3">
          <h2 className="text-sm font-semibold tracking-wide">XAU/USD</h2>
          <span className="text-xs text-faint">Or spot · prix moyen bid/ask</span>
        </div>
        <div className="flex items-center gap-2">
          <Toggle on={showLevels} onClick={() => setShowLevels((v) => !v)} label="Niveaux" />
          <Toggle on={showStructure} onClick={() => setShowStructure((v) => !v)} label="Structure" />
          <Segmented label="Unité de temps" value={tf} options={TIMEFRAMES} onChange={(v) => setTf(v)} />
        </div>
      </div>
      <div className="num flex flex-wrap gap-x-4 gap-y-1 px-5 pt-2 text-xs text-muted" aria-live="polite">
        {r ? (
          <>
            {readout && <span className="text-faint">{readout.t}</span>}
            <span>O <b className="font-medium text-text">{r.o.toFixed(2)}</b></span>
            <span>H <b className="font-medium text-text">{r.h.toFixed(2)}</b></span>
            <span>B <b className="font-medium text-text">{r.l.toFixed(2)}</b></span>
            <span>C <b className="font-medium text-text">{r.c.toFixed(2)}</b></span>
            <span className={chg > 0 ? "text-up" : chg < 0 ? "text-down" : ""}>
              {chg > 0 ? "▲ +" : chg < 0 ? "▼ −" : ""}
              {Math.abs(chg).toFixed(2)}
            </span>
          </>
        ) : (
          <span>&nbsp;</span>
        )}
      </div>
      <div className="relative min-h-0 flex-1 px-2 pb-2">
        <div ref={host} className="absolute inset-0" />
        {empty && (
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-1 text-center">
            <p className="text-sm text-muted">Aucune bougie pour l&apos;instant</p>
            <p className="max-w-xs text-xs text-faint">
              Le graphique se remplit dès que le flux cTrader est connecté, ou après un import Dukascopy.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function Toggle({ on, onClick, label }: { on: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={on}
      className={`rounded-lg border px-2.5 py-1 text-xs transition ${on ? "border-gold/40 bg-gold-soft text-gold" : "border-line text-muted hover:text-text"}`}
    >
      {label}
    </button>
  );
}
