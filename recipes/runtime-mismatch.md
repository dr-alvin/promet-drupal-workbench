# Runtime mismatch

Classification: **hosting-specific**

## Symptom

Composer succeeds locally but CI rejects PHP or extensions.

## Applicability

Local, CI or hosting builds use different runtimes or platform emulation.

## Read-only detection

Collect actual wrapped CLI PHP/extensions, composer platform configuration, CI image/runtime declarations and locked package requirements.

## Evidence required

Version outputs from each executing runtime; composer.lock and CI provenance.

## Remediation options

Align the approved build/runtime with target requirements; resolve dependencies against that supported runtime.

## Data or behavior implications

Changing runtime can affect extensions, cron and CLI workers. Platform emulation does not upgrade the actual runtime.

## Verification

Clean composer install from the approved lock and bootstrap on the deployed lower environment.

## Recovery

Restore the prior matching runtime, code and artifact; include database/files recovery if hooks ran.

## Do not apply

Do not change PHP globally or infer hosting runtime from local PHP; no failure solely from an unrelated host CLI.
