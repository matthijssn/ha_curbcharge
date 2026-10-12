import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.textContent = "";
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = children;
  }

  setAttribute() {}
}

const elements = new Map();
globalThis.HTMLElement = class {
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
    data.rows.slice(0, 8).map(([, value]) => value),
    ["0", "Unknown", "0", "0", "0 kW", "0 EUR/kWh", "0 m", "Closed"],
  );
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
  assert.equal(data.rows[0][1], "Inconnu");
  assert.equal(data.rows[7][1], "Ouverte");
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
    data.rows.map(([, value]) => value),
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
