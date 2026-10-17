const CARD_TYPE = "curbcharge-card";
const EDITOR_TYPE = `${CARD_TYPE}-editor`;
const DEFAULT_CONFIG = Object.freeze({
  layout: "standard",
  show_price: true,
  show_connector_details: true,
  show_station_details: true,
  show_update_times: true,
});
const BOOLEAN_OPTIONS = [
  "show_price",
  "show_connector_details",
  "show_station_details",
  "show_update_times",
];
const EDITOR_LABELS = {
  entity: "entity",
  layout: "layout",
  show_price: "showPrice",
  show_connector_details: "showConnectorDetails",
  show_station_details: "showStationDetails",
  show_update_times: "showUpdateTimes",
};

const TEXT = {
  en: {
    available: "Available",
    selected: "Selected",
    noneAvailable: "None available",
    noCurrentStations: "No current stations",
    unknownAvailability: "Availability unknown",
    unknown: "Unknown",
    unavailable: "Unavailable",
    availableConnectors: "Available connectors",
    occupiedConnectors: "Occupied connectors",
    unavailableConnectors: "Unavailable connectors",
    totalConnectors: "Total connectors",
    maximumPower: "Maximum charging power",
    price: "Price per kWh",
    address: "Address",
    distance: "Distance",
    providerOpenStatus: "Provider-reported open status",
    open: "Open",
    closed: "Closed",
    providerUpdated: "Provider station update",
    integrationUpdated: "Last successful update",
    details: "More details",
    entity: "Station entity",
    layout: "Layout",
    standard: "Standard",
    compact: "Compact",
    showPrice: "Show price",
    showConnectorDetails: "Show connector details",
    showStationDetails: "Show station details",
    showUpdateTimes: "Show update times",
    entityMissing: "The configured entity was not found.",
    wrongEntity: "Choose a CurbCharge status or recommendation sensor.",
    invalidConfig: "Use a CurbCharge status or recommendation sensor and supported display options.",
  },
  nl: {
    available: "Beschikbaar",
    selected: "Geselecteerd",
    noneAvailable: "Geen beschikbaar",
    noCurrentStations: "Geen actuele stations",
    unknownAvailability: "Beschikbaarheid onbekend",
    unknown: "Onbekend",
    unavailable: "Niet beschikbaar",
    availableConnectors: "Beschikbare connectoren",
    occupiedConnectors: "Bezette connectoren",
    unavailableConnectors: "Niet-beschikbare connectoren",
    totalConnectors: "Totaal aantal connectoren",
    maximumPower: "Maximaal laadvermogen",
    price: "Prijs per kWh",
    address: "Adres",
    distance: "Afstand",
    providerOpenStatus: "Openstatus volgens provider",
    open: "Open",
    closed: "Gesloten",
    providerUpdated: "Laatste providerupdate",
    integrationUpdated: "Laatste geslaagde update",
    details: "Meer details",
    entity: "Station-entiteit",
    layout: "Weergave",
    standard: "Standaard",
    compact: "Compact",
    showPrice: "Toon prijs",
    showConnectorDetails: "Toon connectordetails",
    showStationDetails: "Toon stationdetails",
    showUpdateTimes: "Toon updatetijden",
    entityMissing: "De ingestelde entiteit is niet gevonden.",
    wrongEntity: "Kies een CurbCharge-status- of aanbevelingssensor.",
    invalidConfig: "Gebruik een CurbCharge-status- of aanbevelingssensor en ondersteunde weergaveopties.",
  },
  fr: {
    available: "Disponible",
    selected: "Sélectionnée",
    noneAvailable: "Aucun disponible",
    noCurrentStations: "Aucune station actuelle",
    unknownAvailability: "Disponibilité inconnue",
    unknown: "Inconnu",
    unavailable: "Indisponible",
    availableConnectors: "Connecteurs disponibles",
    occupiedConnectors: "Connecteurs occupés",
    unavailableConnectors: "Connecteurs indisponibles",
    totalConnectors: "Total des connecteurs",
    maximumPower: "Puissance de charge maximale",
    price: "Prix par kWh",
    address: "Adresse",
    distance: "Distance",
    providerOpenStatus: "Statut d'ouverture indiqué par le fournisseur",
    open: "Ouverte",
    closed: "Fermée",
    providerUpdated: "Dernière mise à jour du fournisseur",
    integrationUpdated: "Dernière actualisation réussie",
    details: "Plus de détails",
    entity: "Entité de station",
    layout: "Présentation",
    standard: "Standard",
    compact: "Compact",
    showPrice: "Afficher le prix",
    showConnectorDetails: "Afficher les détails des connecteurs",
    showStationDetails: "Afficher les détails de la station",
    showUpdateTimes: "Afficher les heures de mise à jour",
    entityMissing: "L'entité configurée est introuvable.",
    wrongEntity: "Choisissez un capteur de statut ou de recommandation CurbCharge.",
    invalidConfig: "Utilisez un capteur de statut ou de recommandation CurbCharge et des options d'affichage prises en charge.",
  },
  de: {
    available: "Verfügbar",
    selected: "Ausgewählt",
    noneAvailable: "Keine verfügbar",
    noCurrentStations: "Keine aktuellen Stationen",
    unknownAvailability: "Verfügbarkeit unbekannt",
    unknown: "Unbekannt",
    unavailable: "Nicht verfügbar",
    availableConnectors: "Verfügbare Anschlüsse",
    occupiedConnectors: "Belegte Anschlüsse",
    unavailableConnectors: "Nicht verfügbare Anschlüsse",
    totalConnectors: "Anschlüsse gesamt",
    maximumPower: "Maximale Ladeleistung",
    price: "Preis pro kWh",
    address: "Adresse",
    distance: "Entfernung",
    providerOpenStatus: "Vom Anbieter gemeldeter Öffnungsstatus",
    open: "Geöffnet",
    closed: "Geschlossen",
    providerUpdated: "Letzte Aktualisierung des Anbieters",
    integrationUpdated: "Letzte erfolgreiche Aktualisierung",
    details: "Weitere Details",
    entity: "Station-Entität",
    layout: "Layout",
    standard: "Standard",
    compact: "Kompakt",
    showPrice: "Preis anzeigen",
    showConnectorDetails: "Anschlussdetails anzeigen",
    showStationDetails: "Stationsdetails anzeigen",
    showUpdateTimes: "Aktualisierungszeiten anzeigen",
    entityMissing: "Die konfigurierte Entität wurde nicht gefunden.",
    wrongEntity: "Wählen Sie einen CurbCharge-Status- oder Empfehlungssensor aus.",
    invalidConfig: "Verwenden Sie einen CurbCharge-Status- oder Empfehlungssensor und unterstützte Anzeigeoptionen.",
  },
};

function getLanguage(hass) {
  const language = hass?.locale?.language || hass?.language || "en";
  return language.toLowerCase().split(/[-_]/, 1)[0];
}

function getText(hass) {
  return TEXT[getLanguage(hass)] || TEXT.en;
}

function formatNumber(value, hass, unknown) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return unknown;
  }
  const language = hass?.locale?.language || hass?.language || "en";
  return new Intl.NumberFormat(language, { maximumFractionDigits: 2 }).format(
    value,
  );
}

function formatCount(value, hass, unknown) {
  if (!Number.isInteger(value) || value < 0) {
    return unknown;
  }
  const language = hass?.locale?.language || hass?.language || "en";
  return new Intl.NumberFormat(language).format(value);
}

function formatTimestamp(value, hass, unknown) {
  if (typeof value !== "string" || !value) {
    return unknown;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  const language = hass?.locale?.language || hass?.language || "en";
  return new Intl.DateTimeFormat(language, {
    dateStyle: "short",
    timeStyle: "short",
  }).format(date);
}

export function validateConfig(config) {
  if (
    !config ||
    typeof config.entity !== "string" ||
    !config.entity.startsWith("sensor.")
  ) {
    throw new Error(TEXT.en.invalidConfig);
  }
  const normalized = { ...DEFAULT_CONFIG, ...config };
  if (
    !["standard", "compact"].includes(normalized.layout) ||
    BOOLEAN_OPTIONS.some((option) => typeof normalized[option] !== "boolean")
  ) {
    throw new Error(TEXT.en.invalidConfig);
  }
  return normalized;
}

export function buildCardData(entity, hass, config = DEFAULT_CONFIG) {
  const text = getText(hass);
  if (!entity) {
    return { error: text.entityMissing };
  }

  const options = { ...DEFAULT_CONFIG, ...config };
  const attributes = entity.attributes || {};
  const isCurbChargeSensor =
    typeof attributes.provider_station_id === "string" ||
    typeof attributes.result_status === "string" ||
    (typeof attributes.reason === "string" &&
      Object.hasOwn(attributes, "last_successful_update"));
  if (!isCurbChargeSensor) {
    return { error: text.wrongEntity };
  }

  const unavailable =
    entity.state === "unavailable" || attributes.result_status === "unavailable";
  const values = unavailable ? {} : attributes;
  const statusLabels = {
    available: text.available,
    selected: text.selected,
    none_available: text.noneAvailable,
    no_current_stations: text.noCurrentStations,
    unknown_availability: text.unknownAvailability,
    unknown: text.unknown,
    unavailable: text.unavailable,
  };
  const statusKey = unavailable
    ? "unavailable"
    : Object.hasOwn(statusLabels, attributes.result_status)
      ? attributes.result_status
      : Object.hasOwn(statusLabels, entity.state)
        ? entity.state
        : typeof attributes.provider_station_id === "string"
          ? "selected"
          : "unknown";
  const status = statusLabels[statusKey];
  const openStatus =
    typeof values.is_open === "boolean"
      ? values.is_open
        ? text.open
        : text.closed
      : text.unknown;
  const price =
    typeof values.price_per_kwh === "number" &&
    Number.isFinite(values.price_per_kwh) &&
    typeof values.currency === "string" &&
    values.currency
      ? `${formatNumber(values.price_per_kwh, hass, text.unknown)} ${values.currency}/kWh`
      : text.unknown;
  const providerUpdatedAt = Object.hasOwn(attributes, "provider_last_updated")
    ? values.provider_last_updated
    : typeof attributes.result_status === "string" ||
        typeof attributes.reason === "string"
      ? undefined
      : values.last_updated;
  const primaryRows = [
    [
      text.availableConnectors,
      formatCount(values.available_connectors, hass, text.unknown),
    ],
  ];
  if (options.show_price) {
    primaryRows.push([text.price, price]);
  }

  const detailsRows = [];
  if (options.show_connector_details) {
    detailsRows.push(
      [
        text.occupiedConnectors,
        formatCount(values.occupied_connectors, hass, text.unknown),
      ],
      [
        text.unavailableConnectors,
        formatCount(values.unavailable_connectors, hass, text.unknown),
      ],
      [
        text.totalConnectors,
        formatCount(values.total_connectors, hass, text.unknown),
      ],
    );
  }
  if (options.show_station_details) {
    detailsRows.push(
      [
        text.address,
        typeof values.address === "string" && values.address.trim()
          ? values.address
          : text.unknown,
      ],
      [
        text.maximumPower,
        typeof values.max_power_kw === "number" &&
        Number.isFinite(values.max_power_kw)
          ? `${formatNumber(values.max_power_kw, hass, text.unknown)} kW`
          : text.unknown,
      ],
      [
        text.distance,
        typeof values.distance_m === "number" &&
        Number.isFinite(values.distance_m)
          ? `${formatNumber(values.distance_m, hass, text.unknown)} m`
          : text.unknown,
      ],
      [text.providerOpenStatus, openStatus],
    );
  }
  if (options.show_update_times) {
    detailsRows.push(
      [
        text.providerUpdated,
        formatTimestamp(providerUpdatedAt, hass, text.unknown),
      ],
      [
        text.integrationUpdated,
        formatTimestamp(values.last_successful_update, hass, text.unknown),
      ],
    );
  }

  return {
    title:
      values.station_name ||
      attributes.friendly_name ||
      entity.entity_id ||
      "CurbCharge",
    status,
    statusKey,
    layout: options.layout,
    detailsLabel: text.details,
    primaryRows,
    detailsRows,
  };
}

function getEditorSchema(text) {
  return [
    {
      name: "entity",
      required: true,
      selector: { entity: { domain: "sensor" } },
    },
    {
      name: "layout",
      selector: {
        select: {
          options: [
            { value: "standard", label: text.standard },
            { value: "compact", label: text.compact },
          ],
        },
      },
    },
    ...BOOLEAN_OPTIONS.map((name) => ({
      name,
      selector: { boolean: {} },
    })),
  ];
}

function createMetric([labelText, valueText], primary = false) {
  const metric = document.createElement("div");
  metric.className = primary ? "metric primary-metric" : "metric";
  const label = document.createElement("div");
  label.className = "label";
  label.textContent = labelText;
  const value = document.createElement("div");
  value.className = "value";
  value.textContent = valueText;
  metric.append(label, value);
  return metric;
}

class CurbChargeCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TYPE);
  }

  setConfig(config) {
    this._config = validateConfig(config);
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return this._config?.layout === "compact" ? 4 : 5;
  }

  _render() {
    if (!this._config || !this._hass) {
      return;
    }

    const data = buildCardData(
      this._hass.states[this._config.entity],
      this._hass,
      this._config,
    );
    const style = document.createElement("style");
    style.textContent = `
      ha-card {
        display: grid;
        gap: 16px;
        padding: 18px;
        color: var(--primary-text-color);
      }
      ha-card.compact { gap: 10px; padding: 12px; }
      .header {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
      }
      h2 { font-size: 1.2em; line-height: 1.3; margin: 0; }
      .status-badge {
        flex: none;
        border: 1px solid var(--divider-color);
        border-radius: 999px;
        color: var(--secondary-text-color);
        font-size: 0.9em;
        font-weight: 600;
        padding: 4px 10px;
      }
      .status-available { border-color: var(--success-color); color: var(--success-color); }
      .status-none_available { border-color: var(--warning-color); color: var(--warning-color); }
      .primary-grid, .details-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(min(100%, 12rem), 1fr));
        gap: 10px;
      }
      .metric {
        background: var(--secondary-background-color);
        border-radius: var(--ha-card-border-radius, 12px);
        min-width: 0;
        padding: 12px;
      }
      .compact .metric { padding: 9px; }
      .label { color: var(--secondary-text-color); font-size: 0.9em; }
      .value { font-size: 1.05em; font-weight: 600; overflow-wrap: anywhere; }
      .primary-metric .value {
        font-size: clamp(1.4em, 4vw, 2em);
        font-weight: 700;
        line-height: 1.15;
      }
      .details > summary {
        border-top: 1px solid var(--divider-color);
        cursor: pointer;
        font-weight: 600;
        padding: 12px 0 2px;
      }
      .details > summary:focus-visible {
        border-radius: 4px;
        outline: 2px solid var(--primary-color);
        outline-offset: 3px;
      }
      .details-grid { padding-top: 10px; }
      .error { color: var(--error-color); }
    `;
    const card = document.createElement("ha-card");
    card.className = `card ${data.layout || this._config.layout}`;
    this.shadowRoot.replaceChildren(style, card);

    if (data.error) {
      const error = document.createElement("div");
      error.className = "error";
      error.setAttribute("role", "alert");
      error.textContent = data.error;
      card.append(error);
      return;
    }

    const header = document.createElement("div");
    header.className = "header";
    const title = document.createElement("h2");
    title.textContent = data.title;
    const status = document.createElement("span");
    status.className = `status-badge status-${data.statusKey}`;
    status.setAttribute("role", "status");
    status.textContent = data.status;
    header.append(title, status);
    card.append(header);

    const primaryGrid = document.createElement("div");
    primaryGrid.className = "primary-grid";
    for (const row of data.primaryRows) {
      primaryGrid.append(createMetric(row, true));
    }
    card.append(primaryGrid);

    if (data.detailsRows.length > 0) {
      const details = document.createElement("details");
      details.className = "details";
      details.open = false;
      const summary = document.createElement("summary");
      summary.textContent = data.detailsLabel;
      const detailsGrid = document.createElement("div");
      detailsGrid.className = "details-grid";
      for (const row of data.detailsRows) {
        detailsGrid.append(createMetric(row));
      }
      details.append(summary, detailsGrid);
      card.append(details);
    }
  }
}

class CurbChargeCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._form = undefined;
  }

  setConfig(config) {
    this._config = { ...DEFAULT_CONFIG, ...(config || {}) };
    if (this._form) {
      this._form.data = { ...this._config };
    } else {
      this._render();
    }
  }

  set hass(hass) {
    this._hass = hass;
    if (this._form) {
      this._form.hass = hass;
      this._form.schema = getEditorSchema(getText(hass));
    } else {
      this._render();
    }
  }

  _render() {
    if (!this._config || !this._hass || this._form) {
      return;
    }

    const text = getText(this._hass);
    const form = document.createElement("ha-form");
    form.hass = this._hass;
    form.data = { ...this._config };
    form.schema = getEditorSchema(text);
    form.computeLabel = (schema) =>
      getText(this._hass)[EDITOR_LABELS[schema.name]] || schema.name;
    form.addEventListener("value-changed", (event) => {
      event.stopPropagation();
      this._config = {
        ...this._config,
        ...(event.detail?.value || {}),
      };
      this.dispatchEvent(
        new CustomEvent("config-changed", {
          detail: { config: this._config },
          bubbles: true,
          composed: true,
        }),
      );
    });
    this._form = form;
    this.shadowRoot.replaceChildren(form);
  }
}

if (!customElements.get(EDITOR_TYPE)) {
  customElements.define(EDITOR_TYPE, CurbChargeCardEditor);
}
if (!customElements.get(CARD_TYPE)) {
  customElements.define(CARD_TYPE, CurbChargeCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: CARD_TYPE,
  name: "CurbCharge station",
  description: "Availability and details for one CurbCharge station.",
  preview: true,
});
