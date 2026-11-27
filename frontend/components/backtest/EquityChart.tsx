"use client";

import { AreaSeries, ColorType, createChart, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";
import { toDate } from "@/lib/api";

function series(points: [string, number][]): { time: UTCTimestamp; value: number }[] {
  const out: { time: UTCTimestamp; value: number }[] = [];
  for (const [t, v] of points) {
    const time = Math.floor(toDate(t).getTime() / 1000) as UTCTimestamp;
    if (out.length && time <= out[out.length - 1].time) continue; // strictly ascending
    out.push({ time, value: v });
  }
  return out;
}

/** Equity curve (top) and drawdown in % (bottom pane). */
export function EquityChart({ equity, drawdown, initial }: { equity: [string, number][]; drawdown: [string, number][]; initial: number }) {
  const host = useRef<HTMLDivElement>(null);
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
        panes: { separatorColor: "#232937", enableResize: false },
      },
      grid: { vertLines: { color: "#1a1f2a" }, horzLines: { color: "#1a1f2a" } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: false },
      crosshair: { vertLine: { color: "#e0b55b66", labelBackgroundColor: "#1c212c" }, horzLine: { color: "#e0b55b66", labelBackgroundColor: "#1c212c" } },
      localization: { locale: "fr-FR", dateFormat: "dd/MM/yy" },
      handleScroll: false,
      handleScale: false,
    });
    const eq = series(equity);
    const last = eq.length ? eq[eq.length - 1].value : initial;
    const up = last >= initial;
    const e = c.addSeries(AreaSeries, {
      lineColor: up ? "#e0b55b" : "#e76624",
      topColor: up ? "#e0b55b33" : "#e7662433",
      bottomColor: "#e0b55b00",
      lineWidth: 2,
      priceLineVisible: false,
      priceFormat: { type: "price", precision: 0, minMove: 1 },
    });
    e.setData(eq);
    e.createPriceLine({ price: initial, color: "#5b6375", lineStyle: 2, lineWidth: 1, axisLabelVisible: false, title: "capital initial" });
    const d = c.addSeries(
      AreaSeries,
      {
        lineColor: "#eb5757",
        topColor: "#eb575700",
        bottomColor: "#eb575740",
        invertFilledArea: false,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        priceFormat: { type: "custom", formatter: (v: number) => `${v.toFixed(1)} %` },
      },
      1,
    );
    d.setData(series(drawdown));
    c.panes()[1]?.setHeight(90);
    c.timeScale().fitContent();
    return () => c.remove();
  }, [equity, drawdown, initial]);
  return <div ref={host} className="h-[340px] w-full" role="img" aria-label="Courbe de capital et drawdown" />;
}
