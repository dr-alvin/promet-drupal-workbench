# CMS dialog regressions

Classification: **project-specific**

## Symptom

Editor dialogs overflow, misalign or lose focus/controls.

## Applicability

A reproduced CMS dialog problem depends on the installed theme/module combination.

## Read-only detection

Inspect dialog bounds, computed styles, focus, scrolling and accessible controls at affected viewports.

## Evidence required

Role, route, interaction sequence, screenshots, browser diagnostics and theme/module versions.

## Remediation options

Fix the actual theme/module interaction; prefer a supported upstream fix or narrowly scoped local correction.

## Data or behavior implications

CSS changes can affect other dialogs, sticky controls and keyboard behavior.

## Verification

Repeat media, Views, Webform and mode-chooser dialogs where applicable; verify mobile and keyboard controls.

## Recovery

Revert the scoped code/configuration change and rebuild assets.

## Do not apply

Do not distribute Lakeshore selectors as universal Drupal 11 CSS or hide admin toolbars.
