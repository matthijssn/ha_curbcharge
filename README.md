# CurbCharge

CurbCharge discovers public EV charging points near a Home Assistant home, a
manually configured location, or a tracked Home Assistant entity. It creates
one device per charging point and reports provider-supplied availability,
connector counts, power, distance, operator, tariff data when unambiguous, and
a best-nearby recommendation.
Favorites are scoped to a config entry and can be managed with admin-only
actions, monitored with favorite sensors, and shown in the bundled card. See
the [favorite services and automation guide](docs/dashboards-and-automations.md#favorite-services-and-automations).

## Requirements

CurbCharge requires Home Assistant 2024.12.0 or newer; this is the minimum
declared in `hacs.json`. Core 2024.12.0 provides `ConfigEntry.runtime_data` and
requires Python 3.12 or newer, matching the integration's APIs and Python
syntax. CI runs on Python 3.13 with the current Home Assistant test package; it
does not pin the test suite to Core 2024.12.0.

## Coverage

Live coverage is public charging points in the Netherlands through NDW DOT-NL.
Belgium DATEX II is a preview for authorized users and is not release-ready
until access, licensing, quotas, and live-payload compatibility are verified.
See [provider coverage and limitations](docs/usage.md#data-source-and-coverage).

## Installation

### HACS custom repository

[Add CurbCharge to HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=matthijssn&repository=ha_curbcharge&category=integration),
or:

1. In HACS, open **Integrations** and choose **Custom repositories** from the
   menu.
2. Add the GitHub URL for this repository and select **Integration** as the
   category.
3. Search for **CurbCharge**, install it, and restart Home Assistant.

### Manual

Copy `custom_components/curbcharge` into the Home Assistant configuration
directory at `custom_components/curbcharge`, then restart Home Assistant.

## Documentation

- [User guide: coverage, setup, entities, updates, and privacy](docs/usage.md)
- [Dashboards, cards, and automations](docs/dashboards-and-automations.md)
- [Development and releases](docs/development.md)
- [Copyable YAML examples](examples/)

## Support

[Support CurbCharge on Ko-fi](https://ko-fi.com/matthijssn).
