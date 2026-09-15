# Private-files requirements

Classification: **project-specific**

## Symptom

Private file access, uploads or protected downloads fail.

## Applicability

The project actually uses private files in the selected environment.

## Read-only detection

Inspect effective private path, directory access and web-server routing; test unauthenticated direct requests without reading private contents.

## Evidence required

Environment-specific path metadata, access results and representative authorized workflow.

## Remediation options

Configure the approved private storage path and permissions; protect direct web access.

## Data or behavior implications

Storage location affects deployment mounts, permissions and recovery procedures.

## Verification

Authorized upload/download works and direct unauthorized public access is denied.

## Recovery

Restore prior storage settings/mounts and matching files backup.

## Do not apply

Do not hardcode Lakeshore paths, chmod broadly, or copy private files into toolkit evidence.
