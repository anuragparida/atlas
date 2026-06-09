// src/copy/strings.ts
// SPEC §10 — all user-facing strings for the 3-state UX live here.
// The "shipped" set is exported as named constants; the VpnBanner,
// Unreachable screen, and the (future) CompletionBanner component all
// import from this module so the wording has exactly one source of truth
// and a designer/agent can tighten copy in one place.
//
// Voice rules (per SPEC §10 and the Phase 3 plan card):
//   - Signal-dense, candid, no marketing fluff.
//   - No Lorem Ipsum, no "Coming soon.", no placeholder.
//   - One noun phrase where possible; the user is on a phone, in a
//     hurry, mid-thought.

// --- State 2 — VPN banner (visible only when routing via Tailscale) ---

export const VPN_BANNER_TEXT = "via VPN · tap for details";

// Accessibility label is the same wording, with a comma so VoiceOver
// pauses naturally between "via VPN" and the action.
export const VPN_BANNER_A11Y = "via VPN, tap for details";

// Bottom sheet title when the user taps the banner.
export const VPN_SHEET_TITLE = "Routing via Tailscale";

// --- State 3 — Unreachable screen (full screen, no WebView) ---

export const UNREACHABLE_HEADLINE = "Can't reach openclaw";
export const UNREACHABLE_SUB =
  "If you're away from home, turn on Tailscale VPN.";

export const UNREACHABLE_PRIMARY_BUTTON = "Open Tailscale";
export const UNREACHABLE_SECONDARY_BUTTON = "Retry";

// --- Foregrounded-different-chat completion banner ---
// SPEC §10 — shown at the top of the chat screen when a different
// chat finishes while Atlas Chat is foregrounded. The <title> is the
// Open WebUI conversation title, truncated upstream.
// NOTE: CompletionBanner.tsx is not yet in src/. This string is the
// canonical wording so the future component picks it up without a
// second review pass.

export const COMPLETION_BANNER_TEXT = (title: string): string =>
  `Chat finished: ${title} · tap to open`;

// --- Backgrounded completion (system NTFY notification) ---
// SPEC §10 — the atlas-notifier service builds the NTFY payload; the
// iOS NTFY app renders the body. Kept here so the iOS-side and
// server-side can be checked against each other for consistency.
export const BACKGROUND_COMPLETION_TEXT = (title: string): string =>
  `Chat finished: ${title}`;
