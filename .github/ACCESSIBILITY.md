# Accessibility

CurbCharge aims to make charging information understandable and usable through
Home Assistant's interfaces. This statement describes current practices and
known limitations; it is not a claim of conformance with WCAG or another formal
accessibility standard.

## Supported context

CurbCharge is a custom Home Assistant integration. Setup and options use Home
Assistant forms, and charging data is exposed through Home Assistant entities.
The bundled Lovelace card is designed for Home Assistant dashboards. Its
accessibility depends on the Home Assistant frontend, browser, operating system,
and assistive technology in use. CurbCharge supports the Home Assistant versions
listed in [hacs.json](../hacs.json); it does not certify every browser or assistive
technology combination.

When a map or custom-card presentation is not suitable, the same sensor data can
be shown in Home Assistant's native Entities card and other textual dashboard
views.

## Current practices

- Availability and recommendation states have localized text labels; color is
  supplementary.
- The bundled card uses native `details` and `summary` elements for expandable
  information and provides a visible keyboard-focus indicator.
- The card preserves disclosure state and restores keyboard focus when Home
  Assistant updates its data.
- Integration and card text is translated into the nine supported Home
  Assistant languages.

Automated card tests cover disclosure semantics, visible state labels, and
focus preservation. They are not a substitute for testing with real browsers
and assistive technologies.

## Known limitations

- No formal accessibility audit or WCAG conformance evaluation has been
  performed.
- We do not maintain a complete browser, screen-reader, switch-control, or
  mobile assistive-technology test matrix.
- Map presentation and external navigation are provided by Home Assistant,
  browsers, and third-party map applications; their accessibility is outside
  CurbCharge's control.

## Report an accessibility barrier

[Open an issue labeled accessibility](https://github.com/matthijssn/ha_curbcharge/issues/new?labels=accessibility)
and include the Home Assistant and CurbCharge versions, the affected surface,
browser and operating system, assistive technology if relevant, reproduction
steps, and the expected and actual behavior. Remove credentials, exact home
coordinates, and personal information from reports and screenshots. Report
security vulnerabilities through the private process in [SECURITY.md](SECURITY.md),
not in a public issue.
