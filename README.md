# CurbCharge

[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/matthijssn/ha_curbcharge)](https://github.com/matthijssn/ha_curbcharge/releases)
[![License](https://img.shields.io/github/license/matthijssn/ha_curbcharge)](LICENSE)
[![Tests](https://img.shields.io/github/actions/workflow/status/matthijssn/ha_curbcharge/tests.yml?branch=main&label=tests)](https://github.com/matthijssn/ha_curbcharge/actions/workflows/tests.yml)

Know which nearby charger is worth the stop. CurbCharge brings public EV
charging into Home Assistant so you can check reported availability, compare
known prices and charging power, and find a station that fits your priorities
before you head out.

<p align="center">
  <img src="docs/images/curbcharge-home-assistant-banner.png" alt="CurbCharge banner showing Home Assistant with a charger map and nearby recommendations" width="720">
</p>

![CurbCharge dashboard showing nearby charger availability and price](docs/images/curbcharge-dashboard-screenshot.png)

## Find the right charger

- **Check before you go:** See reported availability, connector counts,
  charging power, distance, and operator for nearby public chargers.
- **Compare your options:** Find the nearest, cheapest, fastest, or best nearby
  charger. Prices are shown when tariff data is complete and unambiguous;
  unknown prices are not treated as free.
- **Make it personal:** Prefer compatible connectors, power, operators, and
  price. CurbCharge explains when a recommendation uses a fallback.
- **Keep favorites in view:** Monitor saved stations and build automations
  around availability or price changes.
- **Estimate your charge:** Optionally show estimated cost and time to a target
  battery level in the bundled card, using your EV battery sensors. Estimates
  are hidden by default and depend on the vehicle and tariff data you provide.
- **Choose your dashboard:** Use Home Assistant's built-in cards or the
  bundled CurbCharge card, with copyable dashboard and automation examples.

## Supported regions

Netherlands, Norway, Sweden, United States, Finland, Luxembourg, and
Lithuania. Belgium and Switzerland are marked as previews. Coverage and data
quality vary by provider; see the
[provider coverage and limitations](docs/usage.md#data-source-and-coverage).

## Get started

Install from the HACS default store:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=matthijssn&repository=ha_curbcharge&category=integration)

In Home Assistant, go to **HACS → Integrations**, find **CurbCharge**, and
install it. Restart Home Assistant, then add CurbCharge from
**Settings → Devices & services → Add integration**. Use a Home Assistant
release that meets the minimum listed in [`hacs.json`](hacs.json).

Prefer a manual install? Copy `custom_components/curbcharge` into your Home
Assistant configuration directory, restart Home Assistant, and add the
integration.

## Versioning and compatibility

Beginning with 1.0.0, patch and minor releases will preserve CurbCharge's
documented configuration, provider, device/entity, service, sensor-state, and
dashboard-card contracts. Breaking changes require a major version and
documented upgrade steps. See the [1.x compatibility contract](docs/development.md#1x-compatibility-contract).

## Documentation

- [User guide: coverage, setup, entities, updates, and privacy](docs/usage.md)
- [Dashboards, the card, and automations](docs/dashboards-and-automations.md)
- [Development and releases](docs/development.md)
- [Accessibility statement](.github/ACCESSIBILITY.md)
- [Contributing](.github/CONTRIBUTING.md) | [Code of Conduct](.github/CODE_OF_CONDUCT.md) | [Security policy](.github/SECURITY.md)
- [Copyable YAML examples](examples/)

## Community

Questions, ideas, or show-and-tell? Head to
[GitHub Discussions](https://github.com/matthijssn/ha_curbcharge/discussions).
Found a bug? [Open an issue](https://github.com/matthijssn/ha_curbcharge/issues) instead.

## Support

[Support CurbCharge on Ko-fi](https://ko-fi.com/matthijssn).
