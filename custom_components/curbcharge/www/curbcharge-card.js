const CARD_TYPE = "curbcharge-card";

const TEXT = {
  en: {
    available: "Available",
    noneAvailable: "None available",
    unknown: "Unknown",
    unavailable: "Unavailable",
    availableConnectors: "Available connectors",
    occupiedConnectors: "Occupied connectors",
    unavailableConnectors: "Unavailable connectors",
    totalConnectors: "Total connectors",
    maximumPower: "Maximum charging power",
    price: "Price per kWh",
    distance: "Distance",
    operationalStatus: "Station status",
    open: "Open",
    closed: "Closed",
    providerUpdated: "Provider station update",
    integrationUpdated: "Last successful update",
    entityMissing: "The configured entity was not found.",
    wrongEntity: "Choose a CurbCharge status summary sensor.",
    invalidConfig: "Set entity to a CurbCharge status summary sensor entity ID.",
  },
  nl: {
    available: "Beschikbaar",
    noneAvailable: "Geen beschikbaar",
    unknown: "Onbekend",
    unavailable: "Niet beschikbaar",
    availableConnectors: "Beschikbare connectoren",
    occupiedConnectors: "Bezette connectoren",
    unavailableConnectors: "Niet-beschikbare connectoren",
    totalConnectors: "Totaal aantal connectoren",
    maximumPower: "Maximaal laadvermogen",
    price: "Prijs per kWh",
    distance: "Afstand",
    operationalStatus: "Status laadpunt",
    open: "Open",
    closed: "Gesloten",
    providerUpdated: "Laatste providerupdate",
    integrationUpdated: "Laatste geslaagde update",
    entityMissing: "De ingestelde entiteit is niet gevonden.",
    wrongEntity: "Kies een CurbCharge-statussensor.",
    invalidConfig: "Stel entity in op de ID van een CurbCharge-statussensor.",
  },
  fr: {
    available: "Disponible",
    noneAvailable: "Aucun disponible",
    unknown: "Inconnu",
    unavailable: "Indisponible",
    availableConnectors: "Connecteurs disponibles",
    occupiedConnectors: "Connecteurs occupés",
    unavailableConnectors: "Connecteurs indisponibles",
    totalConnectors: "Total des connecteurs",
    maximumPower: "Puissance de charge maximale",
    price: "Prix par kWh",
    distance: "Distance",
    operationalStatus: "État de la borne",
    open: "Ouverte",
    closed: "Fermée",
    providerUpdated: "Dernière mise à jour du fournisseur",
    integrationUpdated: "Dernière actualisation réussie",
    entityMissing: "L'entité configurée est introuvable.",
    wrongEntity: "Choisissez un capteur de résumé CurbCharge.",
    invalidConfig: "Définissez entity sur l'identifiant d'un capteur de résumé CurbCharge.",
  },
  de: {
    available: "Verfügbar",
    noneAvailable: "Keine verfügbar",
    unknown: "Unbekannt",
    unavailable: "Nicht verfügbar",
    availableConnectors: "Verfügbare Anschlüsse",
    occupiedConnectors: "Belegte Anschlüsse",
    unavailableConnectors: "Nicht verfügbare Anschlüsse",
    totalConnectors: "Anschlüsse gesamt",
    maximumPower: "Maximale Ladeleistung",
    price: "Preis pro kWh",
    distance: "Entfernung",
    operationalStatus: "Status der Ladestation",
    open: "Geöffnet",
    closed: "Geschlossen",
    providerUpdated: "Letzte Aktualisierung des Anbieters",
    integrationUpdated: "Letzte erfolgreiche Aktualisierung",
    entityMissing: "Die konfigurierte Entität wurde nicht gefunden.",
    wrongEntity: "Wählen Sie einen CurbCharge-Statussensor aus.",
    invalidConfig: "Setzen Sie entity auf die ID eines CurbCharge-Statussensors.",
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
  return config;
}

export function buildCardData(entity, hass) {
  const text = getText(hass);
  if (!entity) {
    return { error: text.entityMissing };
  }

  const attributes = entity.attributes || {};
  if (typeof attributes.provider_station_id !== "string") {
    return { error: text.wrongEntity };
  }

  const unavailable = entity.state === "unavailable";
  const values = unavailable ? {} : attributes;
  const stateLabels = {
    available: text.available,
    none_available: text.noneAvailable,
    unknown: text.unknown,
    unavailable: text.unavailable,
  };
  const status = stateLabels[entity.state] || text.unknown;
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

  return {
    title:
      attributes.station_name ||
      attributes.friendly_name ||
      entity.entity_id ||
      "CurbCharge",
    status,
    rows: [
      [text.availableConnectors, formatCount(values.available_connectors, hass, text.unknown)],
      [text.occupiedConnectors, formatCount(values.occupied_connectors, hass, text.unknown)],
      [text.unavailableConnectors, formatCount(values.unavailable_connectors, hass, text.unknown)],
      [text.totalConnectors, formatCount(values.total_connectors, hass, text.unknown)],
      [
        text.maximumPower,
        typeof values.max_power_kw === "number" && Number.isFinite(values.max_power_kw)
          ? `${formatNumber(values.max_power_kw, hass, text.unknown)} kW`
          : text.unknown,
      ],
      [text.price, price],
      [
        text.distance,
        typeof values.distance_m === "number" && Number.isFinite(values.distance_m)
          ? `${formatNumber(values.distance_m, hass, text.unknown)} m`
          : text.unknown,
      ],
      [text.operationalStatus, openStatus],
      [text.providerUpdated, formatTimestamp(attributes.last_updated, hass, text.unknown)],
      [
        text.integrationUpdated,
        formatTimestamp(attributes.last_successful_update, hass, text.unknown),
      ],
    ],
  };
}

class CurbChargeCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
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
    return 5;
  }

  _render() {
    if (!this._config || !this._hass) {
      return;
    }

    const text = getText(this._hass);
    const data = buildCardData(
      this._hass.states[this._config.entity],
      this._hass,
    );
    const style = document.createElement("style");
    style.textContent = `
      ha-card { padding: 16px; }
      .header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
      h2 { font-size: 1.2em; margin: 0 0 12px; }
      .status { font-weight: 600; }
      .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap: 12px; }
      .metric { min-width: 0; }
      .label { color: var(--secondary-text-color); font-size: 0.9em; }
      .value { overflow-wrap: anywhere; }
      .error { color: var(--error-color); }
    `;
    const card = document.createElement("ha-card");
    this.shadowRoot.replaceChildren(style, card);

    if (data.error) {
      const error = document.createElement("div");
      error.className = "error";
      error.textContent = data.error;
      card.append(error);
      return;
    }

    const header = document.createElement("div");
    header.className = "header";
    const title = document.createElement("h2");
    title.textContent = data.title;
    const status = document.createElement("span");
    status.className = "status";
    status.setAttribute("role", "status");
    status.textContent = data.status;
    header.append(title, status);
    card.append(header);

    const grid = document.createElement("div");
    grid.className = "grid";
    for (const [labelText, valueText] of data.rows) {
      const metric = document.createElement("div");
      metric.className = "metric";
      const label = document.createElement("div");
      label.className = "label";
      label.textContent = labelText;
      const value = document.createElement("div");
      value.className = "value";
      value.textContent = valueText;
      metric.append(label, value);
      grid.append(metric);
    }
    card.append(grid);
  }
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
