// Runtime proxy to the backend API. The browser only ever talks to this origin, so the session cookie is
// first-party and no CORS is needed. API_URL is read per request (rewrites would freeze it at build time).
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const FORWARD_REQ = ["content-type", "cookie", "authorization", "user-agent"];
const DROP_RES = new Set(["content-encoding", "content-length", "transfer-encoding", "connection"]);

async function proxy(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const base = process.env.API_URL ?? "http://localhost:8000";
  const { path } = await ctx.params;
  const url = `${base}/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers = new Headers();
  for (const h of FORWARD_REQ) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  const fwd = req.headers.get("x-forwarded-for");
  if (fwd) headers.set("x-forwarded-for", fwd);
  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method: req.method,
      headers,
      body: ["GET", "HEAD"].includes(req.method) ? undefined : await req.arrayBuffer(),
      redirect: "manual",
      cache: "no-store",
    });
  } catch {
    return Response.json({ detail: "backend unavailable" }, { status: 502 });
  }
  const out = new Headers();
  upstream.headers.forEach((v, k) => {
    if (!DROP_RES.has(k) && k !== "set-cookie") out.set(k, v);
  });
  for (const c of upstream.headers.getSetCookie()) out.append("set-cookie", c);
  return new Response(upstream.status === 204 ? null : upstream.body, { status: upstream.status, headers: out });
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as DELETE };
