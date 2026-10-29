import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

class FakeEventTarget {
  constructor() {
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    this.listeners.set(type, [...(this.listeners.get(type) || []), listener]);
  }

  dispatchEvent(event) {
    for (const listener of this.listeners.get(event.type) || []) {
      listener(event);
    }
    return true;
  }
}

class FakeElement extends FakeEventTarget {
  constructor(tagName) {
    super();
    this.tagName = tagName;
    this.children = [];
    this.textContent = "";
    this.attributes = new Map();
    this.dataset = {};
    this.open = false;
  }

  append(...children) {
    for (const child of children) child.parentNode = this;
    this.children.push(...children);
  }

  replaceChildren(...children) {
    for (const child of children) child.parentNode = this;
    this.children = children;
    if (this.tagName === "shadow-root") this.activeElement = null;
  }

  setAttribute(name, value) {
    this.attributes.set(name, value);
  }

  querySelectorAll(selector) {
    const matches = [];
    for (const child of this.children) {
      if (
        (selector === "details" && child.tagName === "details") ||
        (selector === "[data-focus-key]" && child.dataset.focusKey)
      ) {
        matches.push(child);
      }
      matches.push(...child.querySelectorAll(selector));
    }
    return matches;
  }

  focus() {
    let parent = this.parentNode;
    while (parent && parent.tagName !== "shadow-root") parent = parent.parentNode;
    if (parent) parent.activeElement = this;
  }
}

const elements = new Map();
globalThis.HTMLElement = class extends FakeEventTarget {
  constructor() {
    super();
  }

  attachShadow() {
    this.shadowRoot = new FakeElement("shadow-root");
    return this.shadowRoot;
  }
};
globalThis.customElements = {
  define: (name, element) => elements.set(name, element),
  get: (name) => elements.get(name),
};
globalThis.document = {
  createElement: (tagName) => new FakeElement(tagName),
};
globalThis.window = { customCards: [] };

const cardSource = await readFile(
  new URL("../custom_components/curbcharge/www/curbcharge-card.js", import.meta.url),
  "utf8",
);
const cardModule = await import(
  `data:text/javascript;base64,${Buffer.from(`${cardSource}\nexport { TEXT };`).toString("base64")}`
);
const SUPPORTED_LANGUAGES = ["en", "nl", "fr", "de", "nb", "sv", "fi"];

function visibleText(element) {
  if (element.tagName === "style") return "";
  return [element.textContent, ...element.children.map(visibleText)].join(" ");
}

test("requires complete nonempty dictionaries for all seven languages", () => {
  assert.deepEqual(Object.keys(cardModule.TEXT), SUPPORTED_LANGUAGES);
  const englishKeys = Object.keys(cardModule.TEXT.en).sort();
  for (const language of SUPPORTED_LANGUAGES) {
    const text = cardModule.TEXT[language];
    assert.deepEqual(Object.keys(text).sort(), englishKeys, language);
    for (const [key, value] of Object.entries(text)) {
      assert.equal(typeof value, "string", `${language}.${key}`);
      assert.ok(value.trim(), `${language}.${key}`);
    }
  }
});

test("rejects invalid configuration and non-CurbCharge entities", () => {
  const Card = elements.get("curbcharge-card");
  const card = new Card();

  assert.throws(() => card.setConfig({}), /status or recommendation sensor/);
  assert.deepEqual(
    cardModule.buildCardData(
      { entity_id: "sensor.other", state: "1", attributes: {} },
      { language: "en" },
    ),
    { error: "Choose a CurbCharge status or recommendation sensor." },
  );
});

test("keeps entity-only YAML compatible and exposes editor options", () => {
  const config = cardModule.validateConfig({
    entity: "sensor.station_status_summary",
  });
  assert.deepEqual(config, {
    entity: "sensor.station_status_summary",
    layout: "standard",
    show_price: true,
    show_connector_details: true,
    show_station_details: true,
    show_update_times: true,
  });

  const Card = elements.get("curbcharge-card");
  assert.equal(Card.getConfigElement().tagName, "curbcharge-card-editor");
  const Editor = elements.get("curbcharge-card-editor");
  const editor = new Editor();
  editor.hass = { language: "nl" };
  editor.setConfig({});
  const form = editor.shadowRoot.children[0];

  assert.equal(form.tagName, "ha-form");
  assert.equal(form.schema.length, 6);
  assert.equal(form.data.layout, "standard");
  assert.equal(form.computeLabel({ name: "layout" }), "Weergave");

  let changedConfig;
  editor.addEventListener("config-changed", (event) => {
    changedConfig = event.detail.config;
  });
  form.dispatchEvent({
    type: "value-changed",
    detail: {
      value: {
        entity: "sensor.station_status_summary",
        layout: "compact",
        show_price: false,
      },
    },
    stopPropagation() {},
  });

  assert.equal(changedConfig.layout, "compact");
  assert.equal(changedConfig.show_price, false);
  assert.equal(changedConfig.show_connector_details, true);
});

test("localizes editor controls in every supported language", () => {
  const labels = {
    en: {
      entity: "Station entity",
      layout: "Layout",
      standard: "Standard",
      compact: "Compact",
      show_price: "Show price",
      show_connector_details: "Show connector details",
      show_station_details: "Show station details",
      show_update_times: "Show update times",
      details: "More details",
    },
    nl: {
      entity: "Station-entiteit",
      layout: "Weergave",
      standard: "Standaard",
      compact: "Compact",
      show_price: "Toon prijs",
      show_connector_details: "Toon connectordetails",
      show_station_details: "Toon stationdetails",
      show_update_times: "Toon updatetijden",
      details: "Meer details",
    },
    fr: {
      entity: "Entité de station",
      layout: "Présentation",
      standard: "Standard",
      compact: "Compact",
      show_price: "Afficher le prix",
      show_connector_details: "Afficher les détails des connecteurs",
      show_station_details: "Afficher les détails de la station",
      show_update_times: "Afficher les heures de mise à jour",
      details: "Plus de détails",
    },
    de: {
      entity: "Station-Entität",
      layout: "Layout",
      standard: "Standard",
      compact: "Kompakt",
      show_price: "Preis anzeigen",
      show_connector_details: "Anschlussdetails anzeigen",
      show_station_details: "Stationsdetails anzeigen",
      show_update_times: "Aktualisierungszeiten anzeigen",
      details: "Weitere Details",
    },
    nb: {
      entity: "Stasjonsentitet",
      layout: "Oppsett",
      standard: "Standard",
      compact: "Kompakt",
      show_price: "Vis pris",
      show_connector_details: "Vis kontaktdetaljer",
      show_station_details: "Vis stasjonsdetaljer",
      show_update_times: "Vis oppdateringstider",
      details: "Flere detaljer",
    },
    sv: {
      entity: "Stationsentitet",
      layout: "Layout",
      standard: "Standard",
      compact: "Kompakt",
      show_price: "Visa pris",
      show_connector_details: "Visa kontaktdetaljer",
      show_station_details: "Visa stationsdetaljer",
      show_update_times: "Visa uppdateringstider",
      details: "Fler detaljer",
    },
    fi: {
      entity: "Asemaentiteetti",
      layout: "Asettelu",
      standard: "Vakio",
      compact: "Tiivis",
      show_price: "Näytä hinta",
      show_connector_details: "Näytä liitintiedot",
      show_station_details: "Näytä asematiedot",
      show_update_times: "Näytä päivitysajat",
      details: "Lisätiedot",
    },
  };
  const Editor = elements.get("curbcharge-card-editor");

  assert.deepEqual(Object.keys(labels), SUPPORTED_LANGUAGES);
  for (const [language, expected] of Object.entries(labels)) {
    const editor = new Editor();
    editor.setConfig({ entity: "sensor.station_status_summary" });
    editor.hass = { language };
    const form = editor.shadowRoot.children[0];

    for (const [name, label] of Object.entries(expected)) {
      if (name !== "details" && name !== "standard" && name !== "compact") {
        assert.equal(form.computeLabel({ name }), label);
      }
    }
    assert.deepEqual(
      form.schema[1].selector.select.options.map((option) => option.label),
      [expected.standard, expected.compact],
    );
    assert.equal(
      cardModule.buildCardData(
        {
          entity_id: "sensor.station_status_summary",
          state: "unknown",
          attributes: { provider_station_id: "station-1" },
        },
        { language },
      ).detailsLabel,
      expected.details,
    );
  }
});

test("shows reported zeroes and leaves unknown counts unknown", () => {
  const data = cardModule.buildCardData(
    {
      entity_id: "sensor.station_status_summary",
      state: "available",
      attributes: {
        provider_station_id: "station-1",
        station_name: "Station One",
        available_connectors: 0,
        occupied_connectors: null,
        unavailable_connectors: 0,
        total_connectors: 0,
        max_power_kw: 0,
        price_per_kwh: 0,
        currency: "EUR",
        distance_m: 0,
        address: "Main Street 10",
        is_open: false,
      },
    },
    { language: "en" },
  );

  assert.equal(data.title, "Station One");
  assert.equal(data.status, "Available");
  assert.deepEqual(
    data.primaryRows.map(([, value]) => value),
    ["0", "0 EUR/kWh"],
  );
  assert.deepEqual(
    data.detailsRows.map(([, value]) => value),
    [
      "Unknown",
      "0",
      "0",
      "Main Street 10",
      "0 kW",
      "0 m",
      "Closed",
      "Unknown",
      "Unknown",
    ],
  );
});

test("renders recommendation payload and separates provider and HA timestamps", () => {
  const providerTime = "2025-01-02T03:04:05Z";
  const refreshTime = "2025-01-02T04:05:06Z";
  const data = cardModule.buildCardData(
    {
      entity_id: "sensor.best_nearby_charger",
      state: "Station One",
      attributes: {
        result_status: "selected",
        provider_station_id: "station-1",
        station_name: "Station One",
        operator: "Example CPO",
        address: "Main Street 10",
        available_connectors: 2,
        total_connectors: 4,
        distance_m: 120,
        max_power_kw: 150,
        price_per_kwh: 0.42,
        currency: "EUR",
        provider_last_updated: providerTime,
        last_updated: refreshTime,
        last_successful_update: refreshTime,
      },
    },
    { language: "en" },
  );

  assert.equal(data.title, "Station One");
  assert.equal(data.status, "Selected");
  assert.deepEqual(data.primaryRows, [
    ["Available connectors", "2"],
    ["Price per kWh", "0.42 EUR/kWh"],
  ]);
  assert.deepEqual(
    data.detailsRows.slice(2, 7),
    [
      ["Total connectors", "4"],
      ["Operator", "Example CPO"],
      ["Address", "Main Street 10"],
      ["Maximum charging power", "150 kW"],
      ["Distance", "120 m"],
    ],
  );
  assert.notEqual(data.detailsRows[8][1], data.detailsRows[9][1]);
  assert.equal(
    data.detailsRows[8][1],
    new Intl.DateTimeFormat("en", {
      dateStyle: "short",
      timeStyle: "short",
    }).format(new Date(providerTime)),
  );
  assert.equal(
    data.detailsRows[9][1],
    new Intl.DateTimeFormat("en", {
      dateStyle: "short",
      timeStyle: "short",
    }).format(new Date(refreshTime)),
  );
});

test("renders a server-ordered recommendation list with unknown and zero values", () => {
  const data = cardModule.buildCardData(
    {
      entity_id: "sensor.top_nearby_chargers",
      state: 2,
      attributes: {
        friendly_name: "Top nearby chargers",
        result_status: "selected",
        recommendations: [
          {
            name: "Station Two",
            rank: 2,
            score: 50,
            reason: "best_nearby_rank",
            available_connectors: null,
            price_per_kwh: null,
            currency: null,
            max_power_kw: null,
            distance_m: null,
          },
          {
            name: "Station One",
            rank: 1,
            score: 100,
            reason: "best_nearby_rank",
            available_connectors: 0,
            price_per_kwh: 0,
            currency: "EUR",
            max_power_kw: 0,
            distance_m: 0,
          },
        ],
      },
    },
    { language: "en" },
  );

  assert.equal(data.title, "Top nearby chargers");
  assert.equal(data.status, "Selected");
  assert.deepEqual(data.primaryRows, []);
  assert.deepEqual(data.detailsRows, []);
  assert.deepEqual(data.recommendations.map(({ name }) => name), [
    "Station Two",
    "Station One",
  ]);
  assert.deepEqual(data.recommendations[0].rows, [
    ["Rank", "2"],
    ["Score", "50"],
    ["Reason", "Ranked by Best nearby"],
    ["Available connectors", "Unknown"],
    ["Price per kWh", "Unknown"],
    ["Maximum charging power", "Unknown"],
    ["Distance", "Unknown"],
  ]);
  assert.deepEqual(data.recommendations[1].rows, [
    ["Rank", "1"],
    ["Score", "100"],
    ["Reason", "Ranked by Best nearby"],
    ["Available connectors", "0"],
    ["Price per kWh", "0 EUR/kWh"],
    ["Maximum charging power", "0 kW"],
    ["Distance", "0 m"],
  ]);
});

test("renders Top N as an ordered list without per-result navigation", () => {
  const Card = elements.get("curbcharge-card");
  const card = new Card();
  card.setConfig({ entity: "sensor.top_nearby_chargers" });
  card.hass = {
    language: "en",
    states: {
      "sensor.top_nearby_chargers": {
        entity_id: "sensor.top_nearby_chargers",
        state: 2,
        attributes: {
          result_status: "selected",
          recommendations: [
            { name: "Second in rank", rank: 2, score: 50, reason: "best_nearby_rank" },
            { name: "First in rank", rank: 1, score: 100, reason: "best_nearby_rank" },
          ],
        },
      },
    },
  };

  const renderedCard = card.shadowRoot.children[1];
  const section = renderedCard.children.find(
    (child) => child.className === "recommendations",
  );
  const list = section.children.find((child) => child.tagName === "ol");

  assert.equal(section.children[0].textContent, "Recommendations");
  assert.deepEqual(
    list.children.map((item) => item.children[0].textContent),
    ["Second in rank", "First in rank"],
  );
  assert.equal(
    renderedCard.children.some((child) => child.className === "navigation"),
    false,
  );
});

test("renders a successful empty Top N list and suppresses unavailable stale items", () => {
  const empty = cardModule.buildCardData(
    {
      entity_id: "sensor.top_nearby_chargers",
      state: 0,
      attributes: {
        result_status: "no_current_stations",
        recommendations: [],
      },
    },
    { language: "en" },
  );
  assert.deepEqual(empty.recommendations, []);

  const unavailable = cardModule.buildCardData(
    {
      entity_id: "sensor.top_nearby_chargers",
      state: "unavailable",
      attributes: {
        result_status: "unavailable",
        recommendations: [{ name: "Stale station", rank: 1, score: 100 }],
      },
    },
    { language: "en" },
  );
  assert.equal(unavailable.status, "Unavailable");
  assert.equal(unavailable.recommendations, null);

  const Card = elements.get("curbcharge-card");
  const card = new Card();
  card.setConfig({ entity: "sensor.top_nearby_chargers" });
  card.hass = {
    language: "en",
    states: {
      "sensor.top_nearby_chargers": {
        entity_id: "sensor.top_nearby_chargers",
        state: "unavailable",
        attributes: {
          result_status: "unavailable",
          recommendations: [{ name: "Stale station", rank: 1, score: 100 }],
        },
      },
    },
  };
  assert.doesNotMatch(visibleText(card.shadowRoot), /Stale station/);
});

test("localizes Top N score and reason labels in every supported language", () => {
  const labels = {
    en: {
      topNearby: "Top nearby chargers",
      recommendations: "Recommendations",
      rank: "Rank",
      score: "Score",
      reason: "Reason",
      bestNearbyRanking: "Ranked by Best nearby",
    },
    nl: {
      topNearby: "Top laadpunten in de buurt",
      recommendations: "Aanbevelingen",
      rank: "Rang",
      score: "Score",
      reason: "Reden",
      bestNearbyRanking: "Gerangschikt volgens beste laadpunt in de buurt",
    },
    fr: {
      topNearby: "Meilleures bornes à proximité",
      recommendations: "Recommandations",
      rank: "Rang",
      score: "Score",
      reason: "Motif",
      bestNearbyRanking: "Classée selon la meilleure borne à proximité",
    },
    de: {
      topNearby: "Top-Ladestationen in der Nähe",
      recommendations: "Empfehlungen",
      rank: "Rang",
      score: "Bewertung",
      reason: "Begründung",
      bestNearbyRanking: "Nach Best nearby sortiert",
    },
    nb: {
      topNearby: "Topprangerte ladere i nærheten",
      recommendations: "Anbefalinger",
      rank: "Plassering",
      score: "Poengsum",
      reason: "Begrunnelse",
      bestNearbyRanking: "Rangert etter beste lader i nærheten",
    },
    sv: {
      topNearby: "Högst rankade laddare i närheten",
      recommendations: "Rekommendationer",
      rank: "Placering",
      score: "Poäng",
      reason: "Motivering",
      bestNearbyRanking: "Rangordnad efter bästa laddare i närheten",
    },
    fi: {
      topNearby: "Parhaat lähellä olevat laturit",
      recommendations: "Suositukset",
      rank: "Sija",
      score: "Pisteet",
      reason: "Peruste",
      bestNearbyRanking: "Järjestetty Paras lähellä olevan mukaan",
    },
  };

  assert.deepEqual(Object.keys(labels), SUPPORTED_LANGUAGES);
  for (const [language, expected] of Object.entries(labels)) {
    const data = cardModule.buildCardData(
      {
        entity_id: "sensor.top_nearby_chargers",
        state: 1,
        attributes: {
          result_status: "selected",
          recommendations: [
            { name: "Station", rank: 1, score: 100, reason: "best_nearby_rank" },
          ],
        },
      },
      { language },
    );
    const rows = data.recommendations[0].rows;

    assert.equal(data.title, expected.topNearby);
    assert.equal(data.recommendationsLabel, expected.recommendations);
    assert.deepEqual(rows.find(([label]) => label === expected.rank), [
      expected.rank,
      "1",
    ]);
    assert.deepEqual(rows.find(([label]) => label === expected.score), [
      expected.score,
      "100",
    ]);
    assert.deepEqual(rows.find(([label]) => label === expected.reason), [
      expected.reason,
      expected.bestNearbyRanking,
    ]);
  }
});

test("shows recommendation result states distinctly and suppresses unavailable data", () => {
  const cases = [
    ["no_current_stations", "No current stations", "unknown"],
    ["unknown_availability", "Availability unknown", "unknown"],
    ["none_available", "None available", "unknown"],
    ["no_comparable_price", "Price not comparable", "unknown"],
    ["no_positive_power", "No positive power", "unknown"],
    ["no_known_distance", "Distance unknown", "unknown"],
    ["unavailable", "Unavailable", "unavailable"],
  ];

  for (const [resultStatus, expectedStatus, state] of cases) {
    const data = cardModule.buildCardData(
      {
        entity_id: "sensor.best_nearby_charger",
        state,
        attributes: {
          result_status: resultStatus,
          reason: "no_available_station",
          last_successful_update: "2025-01-02T03:04:05Z",
          ...(resultStatus === "unavailable"
            ? {
                station_name: "Stale Station",
                available_connectors: 3,
                price_per_kwh: 0.4,
                currency: "EUR",
              }
            : {}),
        },
      },
      { language: "en" },
    );

    assert.equal(data.status, expectedStatus);
    assert.equal(data.primaryRows[0][1], "Unknown");
    if (resultStatus === "unavailable") {
      assert.notEqual(data.title, "Stale Station");
      assert.ok(
        [...data.primaryRows, ...data.detailsRows].every(
          ([, value]) => value === "Unknown",
        ),
      );
    }
  }
});

test("localizes recommendation states, operator, and address in every supported language", () => {
  const labels = {
    en: {
      selected: "Selected",
      no_current_stations: "No current stations",
      unknown_availability: "Availability unknown",
      none_available: "None available",
      no_comparable_price: "Price not comparable",
      no_positive_power: "No positive power",
      no_known_distance: "Distance unknown",
      unavailable: "Unavailable",
      operator: "Operator",
      address: "Address",
    },
    nl: {
      selected: "Geselecteerd",
      no_current_stations: "Geen actuele stations",
      unknown_availability: "Beschikbaarheid onbekend",
      none_available: "Geen beschikbaar",
      no_comparable_price: "Prijs niet vergelijkbaar",
      no_positive_power: "Geen positief vermogen",
      no_known_distance: "Afstand onbekend",
      unavailable: "Niet beschikbaar",
      operator: "Exploitant",
      address: "Adres",
    },
    fr: {
      selected: "Sélectionnée",
      no_current_stations: "Aucune station actuelle",
      unknown_availability: "Disponibilité inconnue",
      none_available: "Aucun disponible",
      no_comparable_price: "Prix non comparable",
      no_positive_power: "Aucune puissance positive",
      no_known_distance: "Distance inconnue",
      unavailable: "Indisponible",
      operator: "Exploitant",
      address: "Adresse",
    },
    de: {
      selected: "Ausgewählt",
      no_current_stations: "Keine aktuellen Stationen",
      unknown_availability: "Verfügbarkeit unbekannt",
      none_available: "Keine verfügbar",
      no_comparable_price: "Preis nicht vergleichbar",
      no_positive_power: "Keine positive Leistung",
      no_known_distance: "Entfernung unbekannt",
      unavailable: "Nicht verfügbar",
      operator: "Betreiber",
      address: "Adresse",
    },
    nb: {
      selected: "Valgt",
      no_current_stations: "Ingen oppdaterte stasjoner",
      unknown_availability: "Ukjent tilgjengelighet",
      none_available: "Ingen ledige",
      no_comparable_price: "Prisen kan ikke sammenlignes",
      no_positive_power: "Ingen positiv effekt",
      no_known_distance: "Ukjent avstand",
      unavailable: "Utilgjengelig",
      operator: "Operatør",
      address: "Adresse",
    },
    sv: {
      selected: "Vald",
      no_current_stations: "Inga aktuella stationer",
      unknown_availability: "Okänd tillgänglighet",
      none_available: "Inga lediga",
      no_comparable_price: "Priset kan inte jämföras",
      no_positive_power: "Ingen positiv effekt",
      no_known_distance: "Okänt avstånd",
      unavailable: "Otillgänglig",
      operator: "Operatör",
      address: "Adress",
    },
    fi: {
      selected: "Valittu",
      no_current_stations: "Ei ajantasaisia asemia",
      unknown_availability: "Saatavuus tuntematon",
      none_available: "Ei yhtään vapaata",
      no_comparable_price: "Hinta ei vertailukelpoinen",
      no_positive_power: "Ei positiivista tehoa",
      no_known_distance: "Etäisyys tuntematon",
      unavailable: "Ei käytettävissä",
      operator: "Operaattori",
      address: "Osoite",
    },
  };

  assert.deepEqual(Object.keys(labels), SUPPORTED_LANGUAGES);
  for (const [language, expected] of Object.entries(labels)) {
    const selected = cardModule.buildCardData(
      {
        entity_id: "sensor.best_nearby_charger",
        state: "Station One",
        attributes: {
          result_status: "selected",
          provider_station_id: "station-1",
          operator: "Example CPO",
          address: "Main Street 10",
        },
      },
      { language },
    );
    assert.equal(selected.status, expected.selected);
    assert.deepEqual(
      selected.detailsRows.find(([label]) => label === expected.address),
      [expected.address, "Main Street 10"],
    );
    assert.deepEqual(
      selected.detailsRows.find(([label]) => label === expected.operator),
      [expected.operator, "Example CPO"],
    );

    const noOperator = cardModule.buildCardData(
      {
        entity_id: "sensor.best_nearby_charger",
        state: "Station One",
        attributes: {
          result_status: "selected",
          provider_station_id: "station-1",
          address: "Main Street 10",
        },
      },
      { language },
    );
    assert.equal(
      noOperator.detailsRows.some(([label]) => label === expected.operator),
      false,
    );

    for (const resultStatus of [
      "no_current_stations",
      "unknown_availability",
      "none_available",
      "no_comparable_price",
      "no_positive_power",
      "no_known_distance",
      "unavailable",
    ]) {
      const data = cardModule.buildCardData(
        {
          entity_id: "sensor.best_nearby_charger",
          state: resultStatus === "unavailable" ? "unavailable" : "unknown",
          attributes: { result_status: resultStatus },
        },
        { language },
      );
      assert.equal(data.status, expected[resultStatus]);
    }
  }
});

test("builds navigation links from valid coordinates or an encoded address", () => {
  const attributes = {
    result_status: "selected",
    provider_station_id: "station-1",
    latitude: 0,
    longitude: -73.9,
    address: "Example Street 10",
  };
  const originalAttributes = { ...attributes };
  const data = cardModule.buildCardData(
    {
      entity_id: "sensor.best_nearby_charger",
      state: "Station One",
      attributes,
    },
    { language: "en" },
  );

  assert.deepEqual(data.navigationLinks, [
    {
      label: "Google Maps directions",
      url: "https://www.google.com/maps/dir/?api=1&destination=0%2C-73.9",
    },
    {
      label: "Apple Maps directions",
      url: "https://maps.apple.com/directions?destination=0%2C-73.9",
    },
    {
      label: "Waze navigation",
      url: "https://waze.com/ul?ll=0%2C-73.9&navigate=yes",
    },
    {
      label: "Open map location (not directions)",
      url: "geo:0,-73.9?q=0,-73.9",
    },
  ]);
  assert.deepEqual(attributes, originalAttributes);
  assert.ok(
    data.navigationLinks.every(
      ({ url }) => !url.includes("origin=") && !url.includes("source="),
    ),
  );

  const addressFallback = cardModule.buildCardData(
    {
      entity_id: "sensor.best_nearby_charger",
      state: "Station One",
      attributes: {
        result_status: "selected",
        provider_station_id: "station-1",
        latitude: 91,
        longitude: -181,
        address: "R&D Road 10",
      },
    },
    { language: "en" },
  );
  assert.deepEqual(addressFallback.navigationLinks, [
    {
      label: "Google Maps directions",
      url: "https://www.google.com/maps/dir/?api=1&destination=R%26D%20Road%2010",
    },
    {
      label: "Apple Maps directions",
      url: "https://maps.apple.com/directions?destination=R%26D%20Road%2010",
    },
    {
      label: "Waze navigation",
      url: "https://waze.com/ul?q=R%26D%20Road%2010&navigate=yes",
    },
    {
      label: "Open map location (not directions)",
      url: "geo:0,0?q=R%26D%20Road%2010",
    },
  ]);
});

test("allows navigation for unknown availability and hides unavailable destinations", () => {
  const summary = cardModule.buildCardData(
    {
      entity_id: "sensor.station_status_summary",
      state: "unknown",
      attributes: {
        provider_station_id: "station-1",
        latitude: 0,
        longitude: 0,
      },
    },
    { language: "en" },
  );
  assert.equal(summary.navigationLinks.length, 4);

  const noDestinationStates = [
    {
      entity_id: "sensor.best_nearby_charger",
      state: "unknown",
      attributes: {
        result_status: "no_current_stations",
        latitude: 52,
        longitude: 5,
        address: "Stale address",
      },
    },
    {
      entity_id: "sensor.best_nearby_charger",
      state: "unknown",
      attributes: {
        result_status: "unknown_availability",
        latitude: 52,
        longitude: 5,
      },
    },
    {
      entity_id: "sensor.best_nearby_charger",
      state: "unavailable",
      attributes: {
        result_status: "selected",
        provider_station_id: "station-1",
        latitude: 52,
        longitude: 5,
      },
    },
    {
      entity_id: "sensor.station_status_summary",
      state: "unavailable",
      attributes: {
        provider_station_id: "station-1",
        latitude: 52,
        longitude: 5,
        address: "Stale address",
      },
    },
    {
      entity_id: "sensor.station_status_summary",
      state: "available",
      attributes: {
        provider_station_id: "station-1",
        latitude: 91,
        longitude: 181,
      },
    },
  ];

  for (const entity of noDestinationStates) {
    const data = cardModule.buildCardData(entity, { language: "en" });
    assert.deepEqual(data.navigationLinks, []);
  }
});

test("renders a collapsed, accessible Navigate disclosure with external links", () => {
  const Card = elements.get("curbcharge-card");
  const card = new Card();
  card.setConfig({ entity: "sensor.best_nearby_charger" });
  card.hass = {
    language: "en",
    states: {
      "sensor.best_nearby_charger": {
        entity_id: "sensor.best_nearby_charger",
        state: "Station One",
        attributes: {
          result_status: "selected",
          provider_station_id: "station-1",
          latitude: 0,
          longitude: -73.9,
        },
      },
    },
  };

  const haCard = card.shadowRoot.children[1];
  const navigation = haCard.children.find(
    (child) => child.className === "navigation",
  );
  assert.ok(navigation);
  assert.equal(navigation.open, false);
  assert.equal(navigation.children[0].tagName, "summary");
  assert.equal(navigation.children[0].textContent, "Navigate");
  const links = navigation.children[1].children.map((row) => row.children[0]);
  assert.deepEqual(
    links.map((link) => link.textContent),
    [
      "Google Maps directions",
      "Apple Maps directions",
      "Waze navigation",
      "Open map location (not directions)",
    ],
  );
  assert.ok(
    links.every(
      (link) =>
        link.attributes.get("target") === "_blank" &&
        link.attributes.get("rel") === "noopener noreferrer",
    ),
  );

  card.hass = {
    language: "en",
    states: {
      "sensor.best_nearby_charger": {
        entity_id: "sensor.best_nearby_charger",
        state: "unknown",
        attributes: { result_status: "no_current_stations" },
      },
    },
  };
  assert.equal(
    card.shadowRoot.children[1].children.some(
      (child) => child.className === "navigation",
    ),
    false,
  );
});

test("localizes navigation disclosure and link labels in every supported language", () => {
  const labels = {
    en: [
      "Navigate",
      "Google Maps directions",
      "Apple Maps directions",
      "Waze navigation",
      "Open map location (not directions)",
    ],
    nl: [
      "Navigeren",
      "Routebeschrijving in Google Maps",
      "Routebeschrijving in Apple Kaarten",
      "Navigatie met Waze",
      "Locatie openen in een kaart (geen route)",
    ],
    fr: [
      "Naviguer",
      "Itinéraire avec Google Maps",
      "Itinéraire avec Plans",
      "Navigation avec Waze",
      "Ouvrir l'emplacement sur une carte (sans itinéraire)",
    ],
    de: [
      "Navigieren",
      "Route mit Google Maps",
      "Route mit Apple Karten",
      "Navigation mit Waze",
      "Kartenstandort öffnen (keine Navigation)",
    ],
    nb: [
      "Naviger",
      "Veibeskrivelse med Google Maps",
      "Veibeskrivelse med Apple Maps",
      "Navigasjon med Waze",
      "Åpne kartposisjon (ikke veibeskrivelse)",
    ],
    sv: [
      "Navigera",
      "Vägbeskrivning med Google Maps",
      "Vägbeskrivning med Apple Maps",
      "Navigering med Waze",
      "Öppna plats på karta (inte vägbeskrivning)",
    ],
    fi: [
      "Navigoi",
      "Google Mapsin reittiohjeet",
      "Apple Mapsin reittiohjeet",
      "Navigointi Wazella",
      "Avaa karttasijainti (ei reittiohjeita)",
    ],
  };

  assert.deepEqual(Object.keys(labels), SUPPORTED_LANGUAGES);
  for (const [language, expected] of Object.entries(labels)) {
    const data = cardModule.buildCardData(
      {
        entity_id: "sensor.station_status_summary",
        state: "unknown",
        attributes: {
          provider_station_id: "station-1",
          latitude: 52,
          longitude: 5,
        },
      },
      { language },
    );

    assert.equal(data.navigationLabel, expected[0]);
    assert.deepEqual(
      data.navigationLinks.map(({ label }) => label),
      expected.slice(1),
    );
  }
});

test("uses display options and starts details collapsed", () => {
  const config = {
    entity: "sensor.station_status_summary",
    layout: "compact",
    show_price: false,
    show_connector_details: false,
    show_station_details: false,
    show_update_times: false,
  };
  const data = cardModule.buildCardData(
    {
      entity_id: config.entity,
      state: "available",
      attributes: {
        provider_station_id: "station-1",
        available_connectors: 2,
        price_per_kwh: 0.4,
        currency: "EUR",
      },
    },
    { language: "en" },
    config,
  );
  const Card = elements.get("curbcharge-card");
  const card = new Card();
  card.setConfig(config);
  card.hass = {
    language: "en",
    states: {
      [config.entity]: {
        entity_id: config.entity,
        state: "available",
        attributes: {
          provider_station_id: "station-1",
          available_connectors: 2,
          price_per_kwh: 0.4,
          currency: "EUR",
        },
      },
    },
  };
  const haCard = card.shadowRoot.children[1];

  assert.deepEqual(data.primaryRows, [["Available connectors", "2"]]);
  assert.deepEqual(data.detailsRows, []);
  assert.equal(haCard.className, "card compact");
  assert.doesNotMatch(visibleText(haCard), /Price per kWh|More details/);
});

test("renders keyboard-accessible details collapsed by default", () => {
  const Card = elements.get("curbcharge-card");
  const card = new Card();
  card.setConfig({ entity: "sensor.station_status_summary" });
  card.hass = {
    language: "en",
    states: {
      "sensor.station_status_summary": {
        entity_id: "sensor.station_status_summary",
        state: "available",
        attributes: {
          provider_station_id: "station-1",
          available_connectors: 2,
        },
      },
    },
  };

  const haCard = card.shadowRoot.children[1];
  const details = haCard.children.find((child) => child.tagName === "details");

  assert.ok(details);
  assert.equal(details.open, false);
  assert.equal(details.children[0].tagName, "summary");
  assert.equal(details.children[0].textContent, "More details");
  assert.equal(haCard.children[0].children[1].attributes.get("role"), "status");
});

test("preserves disclosure state and keyboard focus across Home Assistant updates", () => {
  const Card = elements.get("curbcharge-card");
  const card = new Card();
  card.setConfig({ entity: "sensor.station_status_summary" });
  card.hass = {
    language: "en",
    states: {
      "sensor.station_status_summary": {
        entity_id: "sensor.station_status_summary",
        state: "available",
        attributes: {
          provider_station_id: "station-1",
          available_connectors: 2,
        },
      },
    },
  };
  const firstDetails = card.shadowRoot.querySelectorAll("details")[0];
  const firstSummary = card.shadowRoot.querySelectorAll(
    "[data-focus-key]",
  ).find((element) => element.dataset.focusKey === "details-summary");
  firstDetails.open = true;
  firstSummary.focus();

  card.hass = {
    language: "nl",
    states: {
      "sensor.station_status_summary": {
        entity_id: "sensor.station_status_summary",
        state: "available",
        attributes: {
          provider_station_id: "station-1",
          available_connectors: 3,
        },
      },
    },
  };

  const updatedDetails = card.shadowRoot.querySelectorAll("details")[0];
  const updatedSummary = card.shadowRoot.querySelectorAll(
    "[data-focus-key]",
  ).find((element) => element.dataset.focusKey === "details-summary");
  assert.notEqual(updatedDetails, firstDetails);
  assert.equal(updatedDetails.open, true);
  assert.notEqual(updatedSummary, firstSummary);
  assert.equal(card.shadowRoot.activeElement, updatedSummary);
});

test("renders a localized text favorite indicator only for explicit favorites", () => {
  const labels = {
    en: "Favorite",
    nl: "Favoriet",
    fr: "Favori",
    de: "Favorit",
    nb: "Favoritt",
    sv: "Favorit",
    fi: "Suosikki",
  };
  const sources = [
    {
      entity_id: "sensor.station_status_summary",
      state: "unknown",
      attributes: { provider_station_id: "station-1", is_favorite: true },
    },
    {
      entity_id: "sensor.best_nearby_charger",
      state: "Station One",
      attributes: {
        result_status: "selected",
        provider_station_id: "station-1",
        is_favorite: true,
      },
    },
    {
      entity_id: "sensor.station_favorite_status",
      state: "unknown",
      attributes: { provider_station_id: "station-1", is_favorite: true },
    },
  ];

  for (const source of sources) {
    assert.equal(cardModule.buildCardData(source, { language: "en" }).isFavorite, true);
  }

  for (const favoriteState of [false, null, "true", undefined]) {
    const attributes = { provider_station_id: "station-1" };
    if (favoriteState !== undefined) {
      attributes.is_favorite = favoriteState;
    }
    assert.equal(
      cardModule.buildCardData(
        {
          entity_id: "sensor.station_status_summary",
          state: "available",
          attributes,
        },
        { language: "en" },
      ).isFavorite,
      false,
    );
  }

  const Card = elements.get("curbcharge-card");
  assert.deepEqual(Object.keys(labels), SUPPORTED_LANGUAGES);
  for (const [language, label] of Object.entries(labels)) {
    const entity = sources[2];
    const card = new Card();
    card.setConfig({ entity: entity.entity_id });
    card.hass = { language, states: { [entity.entity_id]: entity } };

    const header = card.shadowRoot.children[1].children[0];
    const title = header.children[0];
    assert.ok(visibleText(title).includes(label));
    assert.equal(header.children[1].attributes.get("role"), "status");
  }
});

test("keeps availability separate and localizes status labels", () => {
  const state = {
    entity_id: "sensor.station_status_summary",
    state: "none_available",
    attributes: {
      provider_station_id: "station-1",
      station_name: "Borne",
      available_connectors: null,
      is_open: true,
    },
  };
  const data = cardModule.buildCardData(state, { locale: { language: "fr-FR" } });

  assert.equal(data.status, "Aucun disponible");
  assert.equal(data.primaryRows[0][1], "Inconnu");
  assert.equal(
    data.detailsRows[6][0],
    "Statut d'ouverture indiqué par le fournisseur",
  );
  assert.equal(data.detailsRows[6][1], "Ouverte");
});

test("labels the provider open flag separately from available connectors", () => {
  const data = cardModule.buildCardData(
    {
      entity_id: "sensor.station_status_summary",
      state: "available",
      attributes: {
        provider_station_id: "station-1",
        station_name: "Station One",
        available_connectors: 2,
        is_open: false,
      },
    },
    { language: "en" },
  );

  assert.equal(data.status, "Available");
  assert.deepEqual(data.primaryRows[0], ["Available connectors", "2"]);
  assert.deepEqual(data.detailsRows[6], [
    "Provider-reported open status",
    "Closed",
  ]);
});

test("localizes the provider open-status label in every supported language", () => {
  const labels = {
    en: ["Provider-reported open status", "Closed"],
    nl: ["Openstatus volgens provider", "Gesloten"],
    fr: ["Statut d'ouverture indiqué par le fournisseur", "Fermée"],
    de: ["Vom Anbieter gemeldeter Öffnungsstatus", "Geschlossen"],
    nb: ["Åpningsstatus ifølge leverandøren", "Stengt"],
    sv: ["Öppningsstatus enligt leverantören", "Stängd"],
    fi: ["Palveluntarjoajan ilmoittama avoinna-tila", "Suljettu"],
  };

  assert.deepEqual(Object.keys(labels), SUPPORTED_LANGUAGES);
  for (const [language, [label, closed]] of Object.entries(labels)) {
    const data = cardModule.buildCardData(
      {
        entity_id: "sensor.station_status_summary",
        state: "available",
        attributes: {
          provider_station_id: "station-1",
          available_connectors: 2,
          is_open: false,
        },
      },
      { language },
    );

    assert.equal(data.detailsRows[6][0], label);
    assert.equal(data.detailsRows[6][1], closed);
  }
});

test("shows unavailable station state without reusing stale measurements", () => {
  const data = cardModule.buildCardData(
    {
      entity_id: "sensor.station_status_summary",
      state: "unavailable",
      attributes: {
        provider_station_id: "station-1",
        station_name: "Station One",
        available_connectors: 3,
        price_per_kwh: 0.4,
        currency: "EUR",
        is_favorite: true,
      },
    },
    { language: "en" },
  );

  assert.equal(data.status, "Unavailable");
  assert.equal(data.isFavorite, true);
  assert.deepEqual(
    [...data.primaryRows, ...data.detailsRows].map(([, value]) => value),
    Array(11).fill("Unknown"),
  );
});

test("rerenders updated Home Assistant state without provider requests", () => {
  const Card = elements.get("curbcharge-card");
  const card = new Card();
  card.setConfig({ entity: "sensor.station_status_summary" });

  card.hass = {
    language: "en",
    states: {
      "sensor.station_status_summary": {
        entity_id: "sensor.station_status_summary",
        state: "available",
        attributes: {
          provider_station_id: "station-1",
          station_name: "Station One",
          available_connectors: 1,
        },
      },
    },
  };
  assert.match(visibleText(card.shadowRoot), /1/);

  card.hass = {
    language: "en",
    states: {
      "sensor.station_status_summary": {
        entity_id: "sensor.station_status_summary",
        state: "unknown",
        attributes: {
          provider_station_id: "station-1",
          station_name: "Station One",
          available_connectors: null,
        },
      },
    },
  };
  assert.match(visibleText(card.shadowRoot), /Unknown/);
});

test("switches existing cards and editors through HA locales independently of providers", () => {
  const Card = elements.get("curbcharge-card");
  const Editor = elements.get("curbcharge-card-editor");
  const providerTime = "2026-08-16T12:30:00Z";
  const refreshTime = "2026-08-16T14:45:00Z";
  for (const property of ["locale", "language"]) {
    for (const [provider, country, currency] of [
      ["nobil_no", "NO", "EUR"],
      ["nobil_se", "SE", "NOK"],
    ]) {
      const entity = {
        entity_id: "sensor.custom_station_name",
        state: "available",
        attributes: {
          provider,
          country_code: country,
          provider_station_id: `${country}_synthetic`,
          station_name: "Øresund laddplats",
          friendly_name: "My custom name",
          available_connectors: 1234,
          total_connectors: 2345,
          max_power_kw: 43.25,
          price_per_kwh: 1234.56,
          currency,
          last_updated: providerTime,
          last_successful_update: refreshTime,
          latitude: 0,
          longitude: 0,
        },
      };
      const original = structuredClone(entity);
      const card = new Card();
      const editor = new Editor();
      const config = { entity: entity.entity_id, layout: "compact" };
      card.setConfig(config);
      editor.setConfig(config);
      let originalForm;
      for (const language of ["en", "nb", "sv", "nb-NO", "sv-SE", "fi", "fi-FI", "es-ES", undefined]) {
        const hass = {
          ...(language
            ? property === "locale"
              ? { locale: { language }, language: "en" }
              : { language }
            : {}),
          states: { [entity.entity_id]: entity },
          callApi: () => assert.fail("Language changes must not request provider data"),
          callService: () => assert.fail("Language changes must not change entries"),
        };
        const text = cardModule.TEXT[(language || "en").split("-")[0]] || cardModule.TEXT.en;
        card.hass = hass;
        editor.hass = hass;
        const data = cardModule.buildCardData(entity, hass);
        const form = editor.shadowRoot.children[0];
        originalForm ||= form;

        assert.equal(form, originalForm);
        assert.equal(form.computeLabel({ name: "entity" }), text.entity);
        assert.equal(form.computeLabel({ name: "show_price" }), text.showPrice);
        assert.deepEqual(
          form.schema[1].selector.select.options.map(({ label }) => label),
          [text.standard, text.compact],
        );
        assert.equal(form.data.entity, config.entity);
        assert.equal(form.data.layout, config.layout);
        assert.equal(card._config.entity, config.entity);
        assert.ok(visibleText(card.shadowRoot).includes(text.details));
        assert.ok(visibleText(card.shadowRoot).includes(text.availableConnectors));
        assert.equal(data.title, original.attributes.station_name);
        assert.equal(data.status, text.available);
        assert.equal(data.primaryRows[0][1], new Intl.NumberFormat(language || "en").format(1234));
        assert.equal(
          data.primaryRows[1][1],
          `${new Intl.NumberFormat(language || "en", { maximumFractionDigits: 2 }).format(1234.56)} ${currency}/kWh`,
        );
        assert.deepEqual(
          data.detailsRows.find(([label]) => label === text.maximumPower),
          [text.maximumPower, `${new Intl.NumberFormat(language || "en", { maximumFractionDigits: 2 }).format(43.25)} kW`],
        );
        for (const [label, timestamp] of [
          [text.providerUpdated, providerTime],
          [text.integrationUpdated, refreshTime],
        ]) {
          assert.deepEqual(
            data.detailsRows.find(([rowLabel]) => rowLabel === label),
            [
              label,
              new Intl.DateTimeFormat(language || "en", {
                dateStyle: "short",
                timeStyle: "short",
              }).format(new Date(timestamp)),
            ],
          );
        }
        assert.deepEqual(entity, original);
      }
    }
  }
});

test("keeps positive, zero, unknown, and unavailable data distinct in nb and sv", () => {
  for (const [language, labels] of [
    ["nb", ["Ledig", "Ingen ledige", "Ukjent tilgjengelighet", "Utilgjengelig", "Ukjent"]],
    ["sv", ["Ledig", "Inga lediga", "Okänd tillgänglighet", "Otillgänglig", "Okänt"]],
  ]) {
    const cases = [
      ["available", 2, labels[0], "2"],
      ["none_available", 0, labels[1], "0"],
      ["unknown_availability", null, labels[2], labels[4]],
      ["unavailable", 2, labels[3], labels[4]],
    ];
    for (const [status, availableCount, expectedStatus, expectedCount] of cases) {
      const data = cardModule.buildCardData(
        {
          entity_id: "sensor.station_status_summary",
          state: status === "unavailable" ? "unavailable" : "unknown",
          attributes: {
            provider_station_id: "synthetic-station",
            result_status: status,
            available_connectors: availableCount,
            price_per_kwh: 0,
            currency: "EUR",
          },
        },
        { language },
      );
      assert.equal(data.status, expectedStatus);
      assert.equal(data.statusKey, status);
      assert.equal(data.primaryRows[0][1], expectedCount);
      assert.equal(
        data.primaryRows[1][1],
        status === "unavailable" ? labels[4] : "0 EUR/kWh",
      );
    }
  }
});

test("localizes missing and wrong entity errors in nb and sv", () => {
  for (const [language, missing, wrong] of [
    [
      "nb",
      "Den konfigurerte entiteten ble ikke funnet.",
      "Velg en status- eller anbefalingssensor fra CurbCharge.",
    ],
    [
      "sv",
      "Den konfigurerade entiteten hittades inte.",
      "Välj en status- eller rekommendationssensor från CurbCharge.",
    ],
  ]) {
    assert.deepEqual(cardModule.buildCardData(undefined, { language }), { error: missing });
    assert.deepEqual(
      cardModule.buildCardData(
        { entity_id: "sensor.other", state: "1", attributes: {} },
        { language },
      ),
      { error: wrong },
    );
  }
});
