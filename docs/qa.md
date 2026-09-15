# QA and business acceptance

Use the approved non-production URL and assigned role. Record the tested release, viewport, scenario ID, actual result and screenshot/defect reference. Use synthetic content for uploads or editing. Do not save, publish, delete or change permissions unless the test explicitly authorizes disposable fixtures and cleanup.

1. Check representative public templates, navigation, images, search, forms and consent behavior on desktop, tablet and mobile.
2. Sign in with each configured role. Confirm navigation and restricted access; a login redirect or error page is a failure.
3. Add/edit/preview representative content without saving where possible. Confirm the expected text format and CKEditor controls.
4. Open media selection/upload dialogs; check modal position, viewport bounds, scrolling, focus and controls. Only upload when fixture changes are authorized.
5. Where installed and in scope, inspect block placement, Webform creation/help, Views add/rearrange, and form/view-mode chooser/add/edit dialogs.
6. Compare before/after screenshots, but also inspect expected content, usable controls and keyboard behavior. Differences require review; do not approve all changes simply because an upgrade occurred.
7. Record pre-existing issues separately from new defects. Mark untested areas blocked/skipped with the reason; never passed.

Business UAT approval must identify the exact release candidate/build, environment, named approver, date, accepted scenario IDs and defect disposition. Failed critical tests or missing required coverage block release. Production handoff additionally requires a successful representative deployment and recovery rehearsal.
