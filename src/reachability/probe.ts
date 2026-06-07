// src/reachability/probe.ts
// SPEC §6.3 — fetch-with-timeout primitive.
// Each probe hits `${url}/health` and resolves to whether the host
// returned a 2xx within 1s. Anything else (timeout, network error,
// non-2xx) is treated as "not reachable" so the state machine can
// move on to the next candidate.

export const LAN_URL = "http://192.168.178.123:9875";
export const TAILSCALE_URL = "http://100.83.146.18:9875";

export async function probe(url: string): Promise<boolean> {
  try {
    const r = await fetch(`${url}/health`, {
      signal: AbortSignal.timeout(1000),
    });
    return r.ok;
  } catch {
    return false;
  }
}
