# Visual and CMS verification

## Engine and reproducibility

The digest-pinned BackstopJS 6.3.25 container supplies Node, Playwright and Chromium. Helpers have their own exact package-lock.json. The Dockerfile verifies and patches one upstream TLS-default line: upstream 6.3.25 creates contexts with HTTPS errors ignored even when false is passed. Our contexts verify TLS, and configured local exceptions are handled per hostname using CDP certificate events.

Compose runs with the invoking UID/GID, a writable temporary HOME/cache, an init process and 1 GB shared memory. Chromium sandboxing is enabled by default. Root cannot launch it; use a non-root host UID. `browserSandbox:false` is allowed only with a documented `sandboxExceptionReason` after investigating the selected platform. It is not needed in the tested macOS ARM64 configuration.

Baseline settings record scenario definitions, role settings, locale, timezone, engine/image, architecture, stability coverage and browser-helper implementation hashes. Reference image hashes are checked before comparison. Changing them requires a new baseline. Thresholds are mismatch percentages and must be tuned to the project; the example uses 0.1 percent.

Captures are **not** required to share dimensions: `requireSameDimensions` defaults to false, so only the overlapping region is compared and a page that grew taller has its extra content silently left out of the pixel comparison. That gap is closed by the geometry review below, which reports a height change as its own finding rather than letting uncompared content read as agreement.

## Comparison accuracy

A single mismatch percentage cannot separate a page that re-rasterised slightly from a component that broke, and both routinely land in the same fraction of a percent. Two mechanisms address this.

`ignoreAntialiasing` is enabled by default in `resembleOutputOptions`. Sub-pixel text rendering is the dominant source of false positives across a core upgrade, and suppressing it is what makes a tight threshold usable rather than forcing it to be loosened until real defects fit underneath.

After comparison, `scripts/geometry-review.js` re-examines every pair that actually differs — Backstop's own MD5 fast path already proves byte-identical pairs, and those are skipped — and classifies the difference by where it is:

| Verdict | Meaning | Effect |
| :--- | :--- | :--- |
| `localized` | a dense contiguous region changed (default: 4096px², a 64×64 block) | fails, even when the overall percentage is below threshold |
| `widespread` | change spread across more than 2 percent of sampled pixels | fails |
| `dimension` | capture height moved more than 24px, so part of the page was never compared | fails |
| `diffuse` | scattered change with no substantial cluster | reported as likely noise |

The review may **escalate** a pass to a failure; this is the point, since a broken control on a long page is a fraction of a percent and a flat threshold structurally cannot catch it. It does not downgrade a failure unless `diffGeometry.allowDiffuseDowngrade` is set explicitly. Thresholds live under `diffGeometry` (`localizedClusterPx`, `diffuseCeilingRatio`, `heightTolerancePx`, `cellSize`, `stride`, `channelTolerance`); `diffGeometry.enabled:false` turns the pass off. Findings are written to `diff-geometry.json` and embedded in `result.json`.

## Capture cost

Captures run at `asyncCaptureLimit` 4 by default (`AUDIT_ASYNC_CAPTURE_LIMIT`). Raising it further is bounded by the site container, not the browser; measure before changing it, because capture flake introduced by concurrency degrades accuracy silently.

Baseline stability is proven by re-capturing against the same reference site. That repeat is **sampled** — every `critical` scenario plus an even spread, four by default — instead of duplicating the whole catalogue on every baseline. `stabilitySampleSize` sets the count and `stabilityFullRepeat:true` restores the exhaustive pass. Which scenarios were re-tested is recorded in `stabilityCoverage`, and the sampling policy is part of the baseline identity, so a baseline proven with a sample cannot silently satisfy a run that demands a full repeat.

Mobile capture mirrors `critical` routes plus an even sample rather than every route at a second viewport (`MOBILE_ROUTES`, default 8; `mobile_cap` when building a catalogue). Pass a cap at or above the route count to restore full mirroring, or 0 to disable mobile capture.

Baselines are cached per project under `baseline-cache/`, keyed on the visual-audit image tag (which hashes every engine script and helper), the visual configuration, the project commit and `composer.lock`. A cached baseline is replayed only when every stored image re-hashes to the manifest recorded at capture time; anything missing or altered discards the entry and forces a fresh capture. Entries expire after 24 hours and are never used when the working tree is dirty, because the commit no longer describes what is deployed. Note that **content changes are not fingerprinted** — editing a node changes the page without changing any tracked input, so a replayed baseline can be stale; this surfaces as a visual difference rather than hiding one. Set `D11_BASELINE_CACHE=0` (or `visual.baselineCache:false`) to disable, `D11_BASELINE_CACHE_TTL` to retune. Reuse is recorded in `baseline-reuse.json` and as a `baseline-reused` run event.

## Networking and TLS

Do not replace a site's hostname automatically. Example network configuration:

```json
{
  "network": {
    "shared": "ddev-example_default",
    "extraHosts": ["site.example.test:host-gateway"]
  },
  "localTlsExceptions": ["site.example.test"]
}
```

Use the actual project's Docker network or explicit host mapping, not that example network name. Hostnames must retain virtual-host, cookies, redirects and TLS semantics. Host-gateway does not guarantee access to a Linux service bound only to 127.0.0.1. The first browser navigation tests connectivity from inside the container and must succeed before capture. Sitemap HTTPS fetches use normal Node certificate validation; browser-local exceptions do not weaken sitemap TLS.

The fixture suite uses host.docker.internal intentionally to reach a fixture bound to all host interfaces, not by rewriting an application hostname. Actual DDEV/Docksal/Lando routing and Linux host-gateway behavior need project/platform validation.

## Scenarios

Every scenario needs a unique ID, label, viewport, relative path OR explicit reference/test URL pair, expected final URL, required page elements and a readiness selector. Empty sets, unknown selections and identical absolute endpoint pairs are rejected. Relative paths compare the same route at configured reference/test sites.

`expectedUrl` is a relative/absolute string, or `{ "reference": "/old", "test": "/new" }` for explicit route comparisons. Role authentication requires login path, credential field/submit selectors, a success selector and environment variable names. A login redirect is a failure even if it renders an attractive screenshot.

`setup`, `interactions`, `assertions`, and `cleanup` contain action/selector objects. Supported actions are click, fill, select, press, check, wait, assertVisible, assertText and upload. Fill/select/press/assertText use `value`; upload uses a configuration-relative `file`. Interaction selectors and expected text are project-specific.

Example non-saving dialog interaction:

```json
{
  "interactions": [
    {"action":"click","selector":"#open"},
    {"action":"fill","selector":"#title","value":"Synthetic preview"}
  ],
  "assertions": [{"action":"assertVisible","selector":"dialog[open] #close"}],
  "cleanup": [{"action":"click","selector":"#close"}]
}
```

Saving clicks/fixture changes must be declared with `mutates:true`, plus `environment.disposable:true`, `environment.allowFixtureMutation:true`, and cleanup. Uploads enforce this automatically. The toolkit cannot infer whether an arbitrary click saves content; the scenario reviewer must classify it correctly. Cleanup runs after the screenshot in the same authenticated page and is attempted on readiness failure. Cleanup/screenshot failure invalidates capture evidence; inspect failed fixtures manually if cleanup could not complete.

Readiness waits for required selectors, fonts, rendered images within the captured document area, and `ready.requiredImages` selectors. Bounded lazy-load scrolling runs by default and can be configured by iterations, distance and elapsed time. Hidden image variants are recorded outside coverage; required hidden images need an interaction that reveals them. Visible broken images, other browser/HTTP errors, blank or unexpected content and timeouts cannot become accepted captures. Changed capture code requires a new baseline directory; preserve earlier failed baselines. Animations/transitions/caret blinking are frozen without stripping toolbars, banners, timestamps or sticky positioning. Masks hide only specified elements while preserving layout and require a reason.

## Sitemap and reports

Sitemap extraction supports namespaces, bounded indexes, gzip, deterministic deduplication and sorting, query parameters, explicit hosts and asset exclusion. DTD/entity declarations are rejected. Fetches, sizes and traversal are bounded; no homepage fallback is used for empty extraction. Sitemap routes supplement, rather than replace, the scenario catalog.

Each run writes result.json, developer/QA Markdown and Backstop HTML/JUnit outputs when available. Valid-capture records are written only after successful screenshot/cleanup. The report archive includes only known report/image paths, excludes symlinks and does not contain role environment values or browser sessions. Review visible screenshot content before distribution.

Mark a small scenario subset `critical: true` for early test feedback. A passing critical set is followed by the complete catalog. Failed or selected-only runs keep partial coverage explicit and do not declare full verification passed. Matching container build inputs reuse the existing image; changed inputs trigger a build. See `launcher-metrics.json`, `image-coverage.jsonl` and `result.json` for measured runtime and coverage.
