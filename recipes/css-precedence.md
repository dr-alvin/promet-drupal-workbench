# CSS precedence regressions

Classification: **project-specific**

## Symptom

Styles regress despite apparently correct theme CSS.

## Applicability

Competing theme, inline or Asset Injector rules affect the reproduced page.

## Read-only detection

Inspect computed styles, cascade order, specificity, aggregation and injector configuration.

## Evidence required

Affected element/page, winning rule, asset versions and cache/build state.

## Remediation options

Correct the narrow competing rule or its ordering; rebuild required assets.

## Data or behavior implications

Broad overrides can regress unrelated components and responsive states.

## Verification

Repeat affected pages/viewports plus neighboring component variants.

## Recovery

Restore the prior scoped rules and rebuild/redeploy matching assets.

## Do not apply

No blanket !important patches, global selector replacements or hiding meaningful error UI.
