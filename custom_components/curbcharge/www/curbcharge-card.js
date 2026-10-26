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
    favorite: "Favorite",
    selected: "Selected",
    topNearby: "Top nearby chargers",
    recommendations: "Recommendations",
    rank: "Rank",
    score: "Score",
    reason: "Reason",
    bestNearbyRanking: "Ranked by Best nearby",
    noneAvailable: "None available",
    noCurrentStations: "No current stations",
    unknownAvailability: "Availability unknown",
    noComparablePrice: "Price not comparable",
    noPositivePower: "No positive power",
    noKnownDistance: "Distance unknown",
    unknown: "Unknown",
    unavailable: "Unavailable",
    availableConnectors: "Available connectors",
    occupiedConnectors: "Occupied connectors",
    unavailableConnectors: "Unavailable connectors",
    totalConnectors: "Total connectors",
    maximumPower: "Maximum charging power",
    price: "Price per kWh",
    operator: "Operator",
    address: "Address",
    distance: "Distance",
    providerOpenStatus: "Provider-reported open status",
    open: "Open",
    closed: "Closed",
    providerUpdated: "Provider station update",
    integrationUpdated: "Last successful update",
    details: "More details",
    navigate: "Navigate",
    googleDirections: "Google Maps directions",
    appleDirections: "Apple Maps directions",
    wazeNavigation: "Waze navigation",
    openMap: "Open map location (not directions)",
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
    favorite: "Favoriet",
    selected: "Geselecteerd",
    topNearby: "Top laadpunten in de buurt",
    recommendations: "Aanbevelingen",
    rank: "Rang",
    score: "Score",
    reason: "Reden",
    bestNearbyRanking: "Gerangschikt volgens beste laadpunt in de buurt",
    noneAvailable: "Geen beschikbaar",
    noCurrentStations: "Geen actuele stations",
    unknownAvailability: "Beschikbaarheid onbekend",
    noComparablePrice: "Prijs niet vergelijkbaar",
    noPositivePower: "Geen positief vermogen",
    noKnownDistance: "Afstand onbekend",
    unknown: "Onbekend",
    unavailable: "Niet beschikbaar",
    availableConnectors: "Beschikbare connectoren",
    occupiedConnectors: "Bezette connectoren",
    unavailableConnectors: "Niet-beschikbare connectoren",
    totalConnectors: "Totaal aantal connectoren",
    maximumPower: "Maximaal laadvermogen",
    price: "Prijs per kWh",
    operator: "Exploitant",
    address: "Adres",
    distance: "Afstand",
    providerOpenStatus: "Openstatus volgens provider",
    open: "Open",
    closed: "Gesloten",
    providerUpdated: "Laatste providerupdate",
    integrationUpdated: "Laatste geslaagde update",
    details: "Meer details",
    navigate: "Navigeren",
    googleDirections: "Routebeschrijving in Google Maps",
    appleDirections: "Routebeschrijving in Apple Kaarten",
    wazeNavigation: "Navigatie met Waze",
    openMap: "Locatie openen in een kaart (geen route)",
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
    favorite: "Favori",
    selected: "Sélectionnée",
    topNearby: "Meilleures bornes à proximité",
    recommendations: "Recommandations",
    rank: "Rang",
    score: "Score",
    reason: "Motif",
    bestNearbyRanking: "Classée selon la meilleure borne à proximité",
    noneAvailable: "Aucun disponible",
    noCurrentStations: "Aucune station actuelle",
    unknownAvailability: "Disponibilité inconnue",
    noComparablePrice: "Prix non comparable",
    noPositivePower: "Aucune puissance positive",
    noKnownDistance: "Distance inconnue",
    unknown: "Inconnu",
    unavailable: "Indisponible",
    availableConnectors: "Connecteurs disponibles",
    occupiedConnectors: "Connecteurs occupés",
    unavailableConnectors: "Connecteurs indisponibles",
    totalConnectors: "Total des connecteurs",
    maximumPower: "Puissance de charge maximale",
    price: "Prix par kWh",
    operator: "Exploitant",
    address: "Adresse",
    distance: "Distance",
    providerOpenStatus: "Statut d'ouverture indiqué par le fournisseur",
    open: "Ouverte",
    closed: "Fermée",
    providerUpdated: "Dernière mise à jour du fournisseur",
    integrationUpdated: "Dernière actualisation réussie",
    details: "Plus de détails",
    navigate: "Naviguer",
    googleDirections: "Itinéraire avec Google Maps",
    appleDirections: "Itinéraire avec Plans",
    wazeNavigation: "Navigation avec Waze",
    openMap: "Ouvrir l'emplacement sur une carte (sans itinéraire)",
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
    favorite: "Favorit",
    selected: "Ausgewählt",
    topNearby: "Top-Ladestationen in der Nähe",
    recommendations: "Empfehlungen",
    rank: "Rang",
    score: "Bewertung",
    reason: "Begründung",
    bestNearbyRanking: "Nach Best nearby sortiert",
    noneAvailable: "Keine verfügbar",
    noCurrentStations: "Keine aktuellen Stationen",
    unknownAvailability: "Verfügbarkeit unbekannt",
    noComparablePrice: "Preis nicht vergleichbar",
    noPositivePower: "Keine positive Leistung",
    noKnownDistance: "Entfernung unbekannt",
    unknown: "Unbekannt",
    unavailable: "Nicht verfügbar",
    availableConnectors: "Verfügbare Anschlüsse",
    occupiedConnectors: "Belegte Anschlüsse",
    unavailableConnectors: "Nicht verfügbare Anschlüsse",
    totalConnectors: "Anschlüsse gesamt",
    maximumPower: "Maximale Ladeleistung",
    price: "Preis pro kWh",
    operator: "Betreiber",
    address: "Adresse",
    distance: "Entfernung",
    providerOpenStatus: "Vom Anbieter gemeldeter Öffnungsstatus",
    open: "Geöffnet",
    closed: "Geschlossen",
    providerUpdated: "Letzte Aktualisierung des Anbieters",
    integrationUpdated: "Letzte erfolgreiche Aktualisierung",
    details: "Weitere Details",
    navigate: "Navigieren",
    googleDirections: "Route mit Google Maps",
    appleDirections: "Route mit Apple Karten",
    wazeNavigation: "Navigation mit Waze",
    openMap: "Kartenstandort öffnen (keine Navigation)",
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
  nb: {
    available: "Ledig",
    favorite: "Favoritt",
    selected: "Valgt",
    topNearby: "Topprangerte ladere i nærheten",
    recommendations: "Anbefalinger",
    rank: "Plassering",
    score: "Poengsum",
    reason: "Begrunnelse",
    bestNearbyRanking: "Rangert etter beste lader i nærheten",
    noneAvailable: "Ingen ledige",
    noCurrentStations: "Ingen oppdaterte stasjoner",
    unknownAvailability: "Ukjent tilgjengelighet",
    noComparablePrice: "Prisen kan ikke sammenlignes",
    noPositivePower: "Ingen positiv effekt",
    noKnownDistance: "Ukjent avstand",
    unknown: "Ukjent",
    unavailable: "Utilgjengelig",
    availableConnectors: "Ledige ladekontakter",
    occupiedConnectors: "Opptatte ladekontakter",
    unavailableConnectors: "Utilgjengelige ladekontakter",
    totalConnectors: "Ladekontakter totalt",
    maximumPower: "Maksimal ladeeffekt",
    price: "Pris per kWh",
    operator: "Operatør",
    address: "Adresse",
    distance: "Avstand",
    providerOpenStatus: "Åpningsstatus ifølge leverandøren",
    open: "Åpen",
    closed: "Stengt",
    providerUpdated: "Leverandørens siste oppdatering",
    integrationUpdated: "Siste vellykkede oppdatering",
    details: "Flere detaljer",
    navigate: "Naviger",
    googleDirections: "Veibeskrivelse med Google Maps",
    appleDirections: "Veibeskrivelse med Apple Maps",
    wazeNavigation: "Navigasjon med Waze",
    openMap: "Åpne kartposisjon (ikke veibeskrivelse)",
    entity: "Stasjonsentitet",
    layout: "Oppsett",
    standard: "Standard",
    compact: "Kompakt",
    showPrice: "Vis pris",
    showConnectorDetails: "Vis kontaktdetaljer",
    showStationDetails: "Vis stasjonsdetaljer",
    showUpdateTimes: "Vis oppdateringstider",
    entityMissing: "Den konfigurerte entiteten ble ikke funnet.",
    wrongEntity: "Velg en status- eller anbefalingssensor fra CurbCharge.",
    invalidConfig: "Bruk en status- eller anbefalingssensor fra CurbCharge og støttede visningsvalg.",
  },
  sv: {
    available: "Ledig",
    favorite: "Favorit",
    selected: "Vald",
    topNearby: "Högst rankade laddare i närheten",
    recommendations: "Rekommendationer",
    rank: "Placering",
    score: "Poäng",
    reason: "Motivering",
    bestNearbyRanking: "Rangordnad efter bästa laddare i närheten",
    noneAvailable: "Inga lediga",
    noCurrentStations: "Inga aktuella stationer",
    unknownAvailability: "Okänd tillgänglighet",
    noComparablePrice: "Priset kan inte jämföras",
    noPositivePower: "Ingen positiv effekt",
    noKnownDistance: "Okänt avstånd",
    unknown: "Okänt",
    unavailable: "Otillgänglig",
    availableConnectors: "Lediga laddkontakter",
    occupiedConnectors: "Upptagna laddkontakter",
    unavailableConnectors: "Otillgängliga laddkontakter",
    totalConnectors: "Totalt antal laddkontakter",
    maximumPower: "Högsta laddeffekt",
    price: "Pris per kWh",
    operator: "Operatör",
    address: "Adress",
    distance: "Avstånd",
    providerOpenStatus: "Öppningsstatus enligt leverantören",
    open: "Öppen",
    closed: "Stängd",
    providerUpdated: "Leverantörens senaste uppdatering",
    integrationUpdated: "Senaste lyckade uppdatering",
    details: "Fler detaljer",
    navigate: "Navigera",
    googleDirections: "Vägbeskrivning med Google Maps",
    appleDirections: "Vägbeskrivning med Apple Maps",
    wazeNavigation: "Navigering med Waze",
    openMap: "Öppna plats på karta (inte vägbeskrivning)",
    entity: "Stationsentitet",
    layout: "Layout",
    standard: "Standard",
    compact: "Kompakt",
    showPrice: "Visa pris",
    showConnectorDetails: "Visa kontaktdetaljer",
    showStationDetails: "Visa stationsdetaljer",
    showUpdateTimes: "Visa uppdateringstider",
    entityMissing: "Den konfigurerade entiteten hittades inte.",
    wrongEntity: "Välj en status- eller rekommendationssensor från CurbCharge.",
    invalidConfig: "Använd en status- eller rekommendationssensor från CurbCharge och visningsalternativ som stöds.",
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

function formatPrice(price, currency, hass, unknown) {
  return typeof price === "number" &&
    Number.isFinite(price) &&
    typeof currency === "string" &&
    currency
    ? `${formatNumber(price, hass, unknown)} ${currency}/kWh`
    : unknown;
}

function formatRecommendation(item, hass, text, options) {
  const recommendation =
    item && typeof item === "object" ? item : {};
  const name =
    typeof recommendation.name === "string" && recommendation.name.trim()
      ? recommendation.name
      : text.unknown;
  const rows = [
    [text.rank, formatCount(recommendation.rank, hass, text.unknown)],
    [text.score, formatNumber(recommendation.score, hass, text.unknown)],
    [
      text.reason,
      recommendation.reason === "best_nearby_rank"
        ? text.bestNearbyRanking
        : text.unknown,
    ],
    [
      text.availableConnectors,
      formatCount(recommendation.available_connectors, hass, text.unknown),
    ],
  ];
  if (options.show_price) {
    rows.push([
      text.price,
      formatPrice(
        recommendation.price_per_kwh,
        recommendation.currency,
        hass,
        text.unknown,
      ),
    ]);
  }
  if (options.show_station_details) {
    rows.push(
      [
        text.maximumPower,
        typeof recommendation.max_power_kw === "number" &&
        Number.isFinite(recommendation.max_power_kw)
          ? `${formatNumber(recommendation.max_power_kw, hass, text.unknown)} kW`
          : text.unknown,
      ],
      [
        text.distance,
        typeof recommendation.distance_m === "number" &&
        Number.isFinite(recommendation.distance_m)
          ? `${formatNumber(recommendation.distance_m, hass, text.unknown)} m`
          : text.unknown,
      ],
    );
  }
  return { name, rows };
}

function buildNavigationLinks(entity, attributes, text) {
  if (
    entity.state === "unavailable" ||
    (typeof attributes.result_status === "string" &&
      attributes.result_status !== "selected")
  ) {
    return [];
  }

  const latitude = attributes.latitude;
  const longitude = attributes.longitude;
  const coordinatesValid =
    typeof latitude === "number" &&
    Number.isFinite(latitude) &&
    latitude >= -90 &&
    latitude <= 90 &&
    typeof longitude === "number" &&
    Number.isFinite(longitude) &&
    longitude >= -180 &&
    longitude <= 180;
  const address =
    typeof attributes.address === "string" ? attributes.address.trim() : "";
  if (!coordinatesValid && !address) {
    return [];
  }

  const destination = coordinatesValid
    ? `${latitude},${longitude}`
    : address;
  const encodedDestination = encodeURIComponent(destination);
  const wazeParameter = coordinatesValid ? "ll" : "q";
  const geoUrl = coordinatesValid
    ? `geo:${destination}?q=${destination}`
    : `geo:0,0?q=${encodedDestination}`;
  return [
    {
      label: text.googleDirections,
      url: `https://www.google.com/maps/dir/?api=1&destination=${encodedDestination}`,
    },
    {
      label: text.appleDirections,
      url: `https://maps.apple.com/directions?destination=${encodedDestination}`,
    },
    {
      label: text.wazeNavigation,
      url: `https://waze.com/ul?${wazeParameter}=${encodedDestination}&navigate=yes`,
    },
    { label: text.openMap, url: geoUrl },
  ];
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
  const hasRecommendationList = Object.hasOwn(attributes, "recommendations");
  const recommendations =
    hasRecommendationList && Array.isArray(values.recommendations)
      ? values.recommendations.map((item) =>
          formatRecommendation(item, hass, text, options),
        )
      : null;
  const statusLabels = {
    available: text.available,
    selected: text.selected,
    none_available: text.noneAvailable,
    no_current_stations: text.noCurrentStations,
    unknown_availability: text.unknownAvailability,
    no_comparable_price: text.noComparablePrice,
    no_positive_power: text.noPositivePower,
    no_known_distance: text.noKnownDistance,
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
  const isFavorite = attributes.is_favorite === true;
  const openStatus =
    typeof values.is_open === "boolean"
      ? values.is_open
        ? text.open
        : text.closed
      : text.unknown;
  const price = formatPrice(
    values.price_per_kwh,
    values.currency,
    hass,
    text.unknown,
  );
  const providerUpdatedAt = Object.hasOwn(attributes, "provider_last_updated")
    ? values.provider_last_updated
    : typeof attributes.result_status === "string" ||
        typeof attributes.reason === "string"
      ? undefined
      : values.last_updated;
  const navigationLinks = buildNavigationLinks(entity, attributes, text);
  const primaryRows = hasRecommendationList
    ? []
    : [
        [
          text.availableConnectors,
          formatCount(values.available_connectors, hass, text.unknown),
        ],
      ];
  if (options.show_price && !hasRecommendationList) {
    primaryRows.push([text.price, price]);
  }

  const detailsRows = [];
  if (options.show_connector_details && !hasRecommendationList) {
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
  if (options.show_station_details && !hasRecommendationList) {
    const operator =
      typeof values.operator === "string" ? values.operator.trim() : "";
    if (operator) {
      detailsRows.push([text.operator, operator]);
    }
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
  if (options.show_update_times && !hasRecommendationList) {
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
      (hasRecommendationList
        ? attributes.friendly_name || text.topNearby
        : values.station_name ||
          attributes.friendly_name ||
          entity.entity_id ||
          "CurbCharge"),
    isFavorite,
    favoriteLabel: text.favorite,
    status,
    statusKey,
    layout: options.layout,
    detailsLabel: text.details,
    navigationLabel: text.navigate,
    navigationLinks,
    primaryRows,
    detailsRows,
    ...(hasRecommendationList
      ? { recommendations, recommendationsLabel: text.recommendations }
      : {}),
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
      .favorite-indicator {
        border: 1px solid var(--primary-color);
        border-radius: 999px;
        color: var(--primary-color);
        display: inline-block;
        font-size: 0.7em;
        font-weight: 600;
        margin-inline-start: 8px;
        padding: 2px 8px;
        vertical-align: middle;
      }
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
      .recommendations { display: grid; gap: 10px; }
      .recommendations-heading { font-size: 1em; margin: 0; }
      .recommendation-list { display: grid; gap: 10px; margin: 0; padding-inline-start: 24px; }
      .recommendation-item {
        background: var(--secondary-background-color);
        border-radius: var(--ha-card-border-radius, 12px);
        padding: 10px 12px;
      }
      .recommendation-name { font-size: 1em; margin: 0 0 8px; }
      .recommendation-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(min(100%, 10rem), 1fr));
        gap: 8px;
      }
      .details > summary, .navigation > summary {
        border-top: 1px solid var(--divider-color);
        cursor: pointer;
        font-weight: 600;
        padding: 12px 0 2px;
      }
      .details > summary:focus-visible, .navigation > summary:focus-visible {
        border-radius: 4px;
        outline: 2px solid var(--primary-color);
        outline-offset: 3px;
      }
      .details-grid { padding-top: 10px; }
      .navigation-links { display: grid; gap: 8px; margin: 10px 0 0; padding-left: 20px; }
      .navigation-links a { color: var(--primary-color); }
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
    if (data.isFavorite) {
      const favorite = document.createElement("span");
      favorite.className = "favorite-indicator";
      favorite.textContent = data.favoriteLabel;
      title.append(favorite);
    }
    const status = document.createElement("span");
    status.className = `status-badge status-${data.statusKey}`;
    status.setAttribute("role", "status");
    status.textContent = data.status;
    header.append(title, status);
    card.append(header);

    if (data.primaryRows.length > 0) {
      const primaryGrid = document.createElement("div");
      primaryGrid.className = "primary-grid";
      for (const row of data.primaryRows) {
        primaryGrid.append(createMetric(row, true));
      }
      card.append(primaryGrid);
    }

    if (Array.isArray(data.recommendations)) {
      const section = document.createElement("section");
      section.className = "recommendations";
      const heading = document.createElement("h3");
      heading.className = "recommendations-heading";
      heading.textContent = data.recommendationsLabel;
      const list = document.createElement("ol");
      list.className = "recommendation-list";
      for (const recommendation of data.recommendations) {
        const item = document.createElement("li");
        item.className = "recommendation-item";
        const name = document.createElement("h4");
        name.className = "recommendation-name";
        name.textContent = recommendation.name;
        const grid = document.createElement("div");
        grid.className = "recommendation-grid";
        for (const row of recommendation.rows) {
          grid.append(createMetric(row));
        }
        item.append(name, grid);
        list.append(item);
      }
      section.append(heading, list);
      card.append(section);
    }

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

    if (data.navigationLinks.length > 0) {
      const navigation = document.createElement("details");
      navigation.className = "navigation";
      navigation.open = false;
      const summary = document.createElement("summary");
      summary.textContent = data.navigationLabel;
      const list = document.createElement("ul");
      list.className = "navigation-links";
      for (const item of data.navigationLinks) {
        const row = document.createElement("li");
        const link = document.createElement("a");
        link.setAttribute("href", item.url);
        link.setAttribute("target", "_blank");
        link.setAttribute("rel", "noopener noreferrer");
        link.textContent = item.label;
        row.append(link);
        list.append(row);
      }
      navigation.append(summary, list);
      card.append(navigation);
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
