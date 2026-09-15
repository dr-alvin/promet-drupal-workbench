# Missing runtime autoloader

Classification: **hosting-specific**

## Symptom

The deployed entrypoint requires autoload_runtime.php but the artifact lacks it.

## Applicability

An entrypoint actually requires this generated file and deployment packaging omits it.

## Read-only detection

Inspect entrypoint, runtime/scaffold generation, allow-plugins, ignore rules and artifact file listing.

## Evidence required

Exact missing path, generation command outcome and packaged artifact manifest.

## Remediation options

Ensure the required generator runs and its output is included in the deployment artifact.

## Data or behavior implications

Artifact generation and packaging policy differ across hosts; a source-control force-add is not generally required.

## Verification

Clean-build the artifact and bootstrap the deployed entrypoint.

## Recovery

Redeploy the previous matching artifact; recover data if deployment hooks changed it.

## Do not apply

Do not force-add generated files universally. Lakeshore’s generated Acquia branch packaging was a project-specific case.
