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
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = children;
  }

  setAttribute(name, value) {
    this.attributes.set(name, value);
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
  `data:text/javascript;base64,${Buffer.from(cardSource).toString("base64")}`
);

function visibleText(element) {
  if (element.tagName === "style") return "";
  return [element.textContent, ...element.children.map(visibleText)].join(" ");
}

test("rejects invalid configuration and non-summary entities", () => {
  const Card = elements.get("curbcharge-card");
  const card = new Card();

  assert.throws(() => card.setConfig({}), /status summary sensor/);
  assert.deepEqual(
    cardModule.buildCardData(
      { entity_id: "sensor.other", state: "1", attributes: {} },
      { language: "en" },
    ),
    { error: "Choose a CurbCharge status summary sensor." },
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
  };
  const Editor = elements.get("curbcharge-card-editor");

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
    ["Unknown", "0", "0", "0 kW", "0 m", "Closed", "Unknown", "Unknown"],
  );
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
    data.detailsRows[5][0],
    "Statut d'ouverture indiqué par le fournisseur",
  );
  assert.equal(data.detailsRows[5][1], "Ouverte");
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
  assert.deepEqual(data.detailsRows[5], [
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
  };

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

    assert.equal(data.detailsRows[5][0], label);
    assert.equal(data.detailsRows[5][1], closed);
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
      },
    },
    { language: "en" },
  );

  assert.equal(data.status, "Unavailable");
  assert.deepEqual(
    [...data.primaryRows, ...data.detailsRows].map(([, value]) => value),
    Array(10).fill("Unknown"),
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
