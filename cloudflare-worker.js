// Cloudflare Worker: serves the platform on your own domain (e.g. eshrahli.com)
// while the app itself runs on PythonAnywhere.
//
// Set two variables in the Worker (Settings → Variables and Secrets):
//   ORIGIN        = https://bo01.eu.pythonanywhere.com
//   PROXY_SECRET  = the same value as PROXY_SECRET in the app's .env   (type: Secret)

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = (env.ORIGIN || "").replace(/\/+$/, "");
    if (!origin) return new Response("ORIGIN is not set", { status: 500 });

    const headers = new Headers(request.headers);
    headers.delete("host");
    headers.set("X-Forwarded-Host", url.host);
    headers.set("X-Forwarded-Proto", "https");
    headers.set("X-Client-IP", request.headers.get("CF-Connecting-IP") || "");
    if (env.PROXY_SECRET) headers.set("X-Proxy-Secret", env.PROXY_SECRET);

    const isStatic = url.pathname.startsWith("/static/");
    const upstream = await fetch(origin + url.pathname + url.search, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
      redirect: "manual",
      // Cache CSS/JS/fonts at Cloudflare for an hour: faster pages, fewer hits on PythonAnywhere
      cf: isStatic ? { cacheEverything: true, cacheTtl: 3600 } : { cacheTtl: 0 },
    });

    const response = new Response(upstream.body, upstream);
    // Keep redirects on your domain
    const loc = response.headers.get("Location");
    if (loc && loc.startsWith(origin)) {
      response.headers.set("Location", loc.replace(origin, `https://${url.host}`));
    }
    return response;
  },
};
