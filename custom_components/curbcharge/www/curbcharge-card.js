const CARD_TYPE = "curbcharge-card";
const EDITOR_TYPE = `${CARD_TYPE}-editor`;
const DEFAULT_CONFIG = Object.freeze({
  layout: "standard",
  show_price: true,
  show_connector_details: true,
  show_station_details: true,
  show_update_times: true,
  enable_ev_estimates: false,
  ev_target_soc: 100,
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
  title: "cardTitle",
  hide_fields: "hideFields",
  show_price: "showPrice",
  show_connector_details: "showConnectorDetails",
  show_station_details: "showStationDetails",
  show_update_times: "showUpdateTimes",
  enable_ev_estimates: "enableEvEstimates",
  ev_soc_entity: "socEntity",
  ev_soc_attribute: "sensorAttribute",
  ev_capacity_entity: "capacityEntity",
  ev_capacity_attribute: "sensorAttribute",
  ev_power_entity: "powerEntity",
  ev_power_attribute: "sensorAttribute",
  ev_target_soc: "targetSoc",
  ev_assumed_power_kw: "assumedPower",
};
const FIELD_LABELS = {
  available_connectors: "availableConnectors",
  price_per_kwh: "price",
  occupied_connectors: "occupiedConnectors",
  unavailable_connectors: "unavailableConnectors",
  total_connectors: "totalConnectors",
  operator: "operator",
  address: "address",
  max_power_kw: "maximumPower",
  distance_m: "distance",
  is_open: "providerOpenStatus",
  provider_last_updated: "providerUpdated",
  provider_timestamp_source: "providerTimestampSource",
  provider_data_age_seconds: "providerDataAge",
  last_successful_update: "integrationUpdated",
  rank: "rank",
  score: "score",
  reason: "reason",
  personalization: "personalized",
  preset: "preset",
  matched_connector_group: "matchedConnectorGroup",
  fallback_reason: "fallbackReason",
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
    providerTimestampSource: "Provider timestamp source",
    providerDataAge: "Provider record age",
    integrationUpdated: "Last successful update",
    details: "More details",
    personalized: "Personalized",
    preset: "Preset",
    balancedPreset: "Balanced",
    lowestPricePreset: "Lowest price",
    shortestDistancePreset: "Shortest distance",
    highestAvailabilityPreset: "Most available connectors",
    highestPowerPreset: "Highest connector power",
    fullMatch: "Full match",
    partialMatch: "Partial match",
    fallback: "Fallback",
    noMatch: "No match",
    noPersonalizedMatch: "No personalized match",
    matchedConnectorGroup: "Matched connector group",
    fallbackReason: "Fallback reason",
    fallbackPreferredOperator: "Preferred operator not matched",
    fallbackUnknownConnectorType: "Connector type unknown",
    fallbackUnknownPower: "Connector power unknown",
    fallbackUnknownOperator: "Operator unknown",
    preferenceMismatch: "Preference not matched",
    navigate: "Navigate",
    googleDirections: "Google Maps directions",
    appleDirections: "Apple Maps directions",
    wazeNavigation: "Waze navigation",
    openMap: "Open map location (not directions)",
    entity: "Station entity",
    layout: "Layout",
    standard: "Standard",
    compact: "Compact",
    cardTitle: "Card title",
    hideFields: "Hide fields",
    showPrice: "Show price",
    showConnectorDetails: "Show connector details",
    showStationDetails: "Show station details",
    showUpdateTimes: "Show update times",
    evEstimates: "EV charging estimates",
    enableEvEstimates: "Show EV estimates",
    socEntity: "Battery state of charge sensor (%)",
    capacityEntity: "Usable battery capacity sensor (kWh)",
    powerEntity: "EV charging power sensor (kW)",
    sensorAttribute: "Sensor attribute (optional)",
    targetSoc: "Target charge (%)",
    assumedPower: "Assumed charging power (kW)",
    energyNeeded: "Battery energy needed",
    estimatedCost: "Estimated cost to target",
    estimatedDuration: "Estimated time to target",
    evEstimateNote: "Estimate excludes charging losses, charging-rate changes, and unreported tariff components.",
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
    providerTimestampSource: "Bron van providertimestamp",
    providerDataAge: "Leeftijd van providergegevens",
    integrationUpdated: "Laatste geslaagde update",
    details: "Meer details",
    personalized: "Gepersonaliseerd",
    preset: "Rangschikking",
    balancedPreset: "Gebalanceerd",
    lowestPricePreset: "Laagste prijs",
    shortestDistancePreset: "Kortste afstand",
    highestAvailabilityPreset: "Meeste beschikbare connectoren",
    highestPowerPreset: "Hoogste connectorvermogen",
    fullMatch: "Volledige match",
    partialMatch: "Gedeeltelijke match",
    fallback: "Terugval",
    noMatch: "Geen match",
    noPersonalizedMatch: "Geen gepersonaliseerde match",
    matchedConnectorGroup: "Passende connectorgroep",
    fallbackReason: "Reden voor terugval",
    fallbackPreferredOperator: "Voorkeursoperator komt niet overeen",
    fallbackUnknownConnectorType: "Connectortype onbekend",
    fallbackUnknownPower: "Vermogen onbekend",
    fallbackUnknownOperator: "Operator onbekend",
    preferenceMismatch: "Voorkeur komt niet overeen",
    navigate: "Navigeren",
    googleDirections: "Routebeschrijving in Google Maps",
    appleDirections: "Routebeschrijving in Apple Kaarten",
    wazeNavigation: "Navigatie met Waze",
    openMap: "Locatie openen in een kaart (geen route)",
    entity: "Station-entiteit",
    layout: "Weergave",
    standard: "Standaard",
    compact: "Compact",
    cardTitle: "Kaarttitel",
    hideFields: "Velden verbergen",
    showPrice: "Toon prijs",
    showConnectorDetails: "Toon connectordetails",
    showStationDetails: "Toon stationdetails",
    showUpdateTimes: "Toon updatetijden",
    evEstimates: "Schatting van EV-laden",
    enableEvEstimates: "EV-schattingen tonen",
    socEntity: "Sensor voor batterijlading (%)",
    capacityEntity: "Sensor voor bruikbare batterijcapaciteit (kWh)",
    powerEntity: "Sensor voor EV-laadvermogen (kW)",
    sensorAttribute: "Sensorattribuut (optioneel)",
    targetSoc: "Doellading (%)",
    assumedPower: "Aangenomen laadvermogen (kW)",
    energyNeeded: "Benodigde batterij-energie",
    estimatedCost: "Geschatte kosten tot doellading",
    estimatedDuration: "Geschatte tijd tot doellading",
    evEstimateNote: "De schatting is exclusief laadverlies, wisselend laadvermogen en niet-gerapporteerde tariefcomponenten.",
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
    providerTimestampSource: "Source de l'horodatage du fournisseur",
    providerDataAge: "Âge des données du fournisseur",
    integrationUpdated: "Dernière actualisation réussie",
    details: "Plus de détails",
    personalized: "Personnalisé",
    preset: "Préréglage",
    balancedPreset: "Équilibré",
    lowestPricePreset: "Prix le plus bas",
    shortestDistancePreset: "Distance la plus courte",
    highestAvailabilityPreset: "Le plus de connecteurs disponibles",
    highestPowerPreset: "Puissance de connecteur la plus élevée",
    fullMatch: "Correspondance complète",
    partialMatch: "Correspondance partielle",
    fallback: "Repli",
    noMatch: "Aucune correspondance",
    noPersonalizedMatch: "Aucune correspondance personnalisée",
    matchedConnectorGroup: "Groupe de connecteurs correspondant",
    fallbackReason: "Motif de repli",
    fallbackPreferredOperator: "Opérateur préféré non trouvé",
    fallbackUnknownConnectorType: "Type de connecteur inconnu",
    fallbackUnknownPower: "Puissance inconnue",
    fallbackUnknownOperator: "Opérateur inconnu",
    preferenceMismatch: "Préférence non satisfaite",
    navigate: "Naviguer",
    googleDirections: "Itinéraire avec Google Maps",
    appleDirections: "Itinéraire avec Plans",
    wazeNavigation: "Navigation avec Waze",
    openMap: "Ouvrir l'emplacement sur une carte (sans itinéraire)",
    entity: "Entité de station",
    layout: "Présentation",
    standard: "Standard",
    compact: "Compact",
    cardTitle: "Titre de la carte",
    hideFields: "Masquer des champs",
    showPrice: "Afficher le prix",
    showConnectorDetails: "Afficher les détails des connecteurs",
    showStationDetails: "Afficher les détails de la station",
    showUpdateTimes: "Afficher les heures de mise à jour",
    evEstimates: "Estimation de recharge du véhicule",
    enableEvEstimates: "Afficher les estimations du véhicule",
    socEntity: "Capteur de charge de la batterie (%)",
    capacityEntity: "Capteur de capacité utile de la batterie (kWh)",
    powerEntity: "Capteur de puissance de recharge du véhicule (kW)",
    sensorAttribute: "Attribut du capteur (facultatif)",
    targetSoc: "Charge cible (%)",
    assumedPower: "Puissance de recharge supposée (kW)",
    energyNeeded: "Énergie à ajouter à la batterie",
    estimatedCost: "Coût estimé jusqu'à la charge cible",
    estimatedDuration: "Durée estimée jusqu'à la charge cible",
    evEstimateNote: "Estimation hors pertes de recharge, variation de puissance et composants tarifaires non déclarés.",
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
    providerTimestampSource: "Quelle des Anbieterzeitstempels",
    providerDataAge: "Alter des Anbieterdatensatzes",
    integrationUpdated: "Letzte erfolgreiche Aktualisierung",
    details: "Weitere Details",
    personalized: "Personalisiert",
    preset: "Rangfolge",
    balancedPreset: "Ausgewogen",
    lowestPricePreset: "Niedrigster Preis",
    shortestDistancePreset: "Kürzeste Entfernung",
    highestAvailabilityPreset: "Meiste verfügbare Anschlüsse",
    highestPowerPreset: "Höchste Anschlussleistung",
    fullMatch: "Vollständige Übereinstimmung",
    partialMatch: "Teilweise Übereinstimmung",
    fallback: "Fallback",
    noMatch: "Keine Übereinstimmung",
    noPersonalizedMatch: "Keine personalisierte Übereinstimmung",
    matchedConnectorGroup: "Passende Anschlussgruppe",
    fallbackReason: "Fallback-Grund",
    fallbackPreferredOperator: "Bevorzugter Betreiber nicht gefunden",
    fallbackUnknownConnectorType: "Anschlusstyp unbekannt",
    fallbackUnknownPower: "Leistung unbekannt",
    fallbackUnknownOperator: "Betreiber unbekannt",
    preferenceMismatch: "Präferenz nicht erfüllt",
    navigate: "Navigieren",
    googleDirections: "Route mit Google Maps",
    appleDirections: "Route mit Apple Karten",
    wazeNavigation: "Navigation mit Waze",
    openMap: "Kartenstandort öffnen (keine Navigation)",
    entity: "Station-Entität",
    layout: "Layout",
    standard: "Standard",
    compact: "Kompakt",
    cardTitle: "Kartentitel",
    hideFields: "Felder ausblenden",
    showPrice: "Preis anzeigen",
    showConnectorDetails: "Anschlussdetails anzeigen",
    showStationDetails: "Stationsdetails anzeigen",
    showUpdateTimes: "Aktualisierungszeiten anzeigen",
    evEstimates: "EV-Ladeschätzung",
    enableEvEstimates: "EV-Schätzungen anzeigen",
    socEntity: "Sensor für Batterieladung (%)",
    capacityEntity: "Sensor für nutzbare Batteriekapazität (kWh)",
    powerEntity: "Sensor für EV-Ladeleistung (kW)",
    sensorAttribute: "Sensorattribut (optional)",
    targetSoc: "Zielladung (%)",
    assumedPower: "Angenommene Ladeleistung (kW)",
    energyNeeded: "Benötigte Batterieenergie",
    estimatedCost: "Geschätzte Kosten bis zum Ziel",
    estimatedDuration: "Geschätzte Zeit bis zum Ziel",
    evEstimateNote: "Schätzung ohne Ladeverluste, wechselnde Ladeleistung und nicht gemeldete Tarifbestandteile.",
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
    providerTimestampSource: "Kilde for leverandørens tidsstempel",
    providerDataAge: "Alder på leverandørdata",
    integrationUpdated: "Siste vellykkede oppdatering",
    details: "Flere detaljer",
    personalized: "Personlig tilpasning",
    preset: "Rangeringsforvalg",
    balancedPreset: "Balansert",
    lowestPricePreset: "Lavest pris",
    shortestDistancePreset: "Kortest avstand",
    highestAvailabilityPreset: "Flest ledige ladekontakter",
    highestPowerPreset: "Høyest kontakteffekt",
    fullMatch: "Fullstendig treff",
    partialMatch: "Delvis treff",
    fallback: "Reserveløsning",
    noMatch: "Ingen treff",
    noPersonalizedMatch: "Ingen personlig tilpasset match",
    matchedConnectorGroup: "Matchende kontaktgruppe",
    fallbackReason: "Årsak til reservevalg",
    fallbackPreferredOperator: "Foretrukket operatør samsvarte ikke",
    fallbackUnknownConnectorType: "Ukjent kontakttype",
    fallbackUnknownPower: "Ukjent effekt",
    fallbackUnknownOperator: "Ukjent operatør",
    preferenceMismatch: "Preferansen samsvarte ikke",
    navigate: "Naviger",
    googleDirections: "Veibeskrivelse med Google Maps",
    appleDirections: "Veibeskrivelse med Apple Maps",
    wazeNavigation: "Navigasjon med Waze",
    openMap: "Åpne kartposisjon (ikke veibeskrivelse)",
    entity: "Stasjonsentitet",
    layout: "Oppsett",
    standard: "Standard",
    compact: "Kompakt",
    cardTitle: "Korttittel",
    hideFields: "Skjul felt",
    showPrice: "Vis pris",
    showConnectorDetails: "Vis kontaktdetaljer",
    showStationDetails: "Vis stasjonsdetaljer",
    showUpdateTimes: "Vis oppdateringstider",
    evEstimates: "Ladeestimat for elbil",
    enableEvEstimates: "Vis estimater for elbil",
    socEntity: "Sensor for batterinivå (%)",
    capacityEntity: "Sensor for brukbar batterikapasitet (kWh)",
    powerEntity: "Sensor for ladeeffekt for elbil (kW)",
    sensorAttribute: "Sensorattributt (valgfritt)",
    targetSoc: "Lademål (%)",
    assumedPower: "Antatt ladeeffekt (kW)",
    energyNeeded: "Energi til batteriet",
    estimatedCost: "Estimert kostnad til lademålet",
    estimatedDuration: "Estimert tid til lademålet",
    evEstimateNote: "Estimatet utelater ladetap, varierende ladeeffekt og ikke-rapporterte tariffkomponenter.",
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
    providerTimestampSource: "Källa för leverantörens tidsstämpel",
    providerDataAge: "Ålder på leverantörsdata",
    integrationUpdated: "Senaste lyckade uppdatering",
    details: "Fler detaljer",
    personalized: "Personlig anpassning",
    preset: "Rangordningsförval",
    balancedPreset: "Balanserad",
    lowestPricePreset: "Lägsta pris",
    shortestDistancePreset: "Kortaste avstånd",
    highestAvailabilityPreset: "Flest lediga laddkontakter",
    highestPowerPreset: "Högsta kontakteffekt",
    fullMatch: "Fullständig matchning",
    partialMatch: "Delvis matchning",
    fallback: "Reservval",
    noMatch: "Ingen matchning",
    noPersonalizedMatch: "Ingen personlig matchning",
    matchedConnectorGroup: "Matchande kontaktgrupp",
    fallbackReason: "Orsak till reservval",
    fallbackPreferredOperator: "Föredragen operatör matchade inte",
    fallbackUnknownConnectorType: "Okänd kontakttyp",
    fallbackUnknownPower: "Okänd effekt",
    fallbackUnknownOperator: "Okänd operatör",
    preferenceMismatch: "Preferensen matchade inte",
    navigate: "Navigera",
    googleDirections: "Vägbeskrivning med Google Maps",
    appleDirections: "Vägbeskrivning med Apple Maps",
    wazeNavigation: "Navigering med Waze",
    openMap: "Öppna plats på karta (inte vägbeskrivning)",
    entity: "Stationsentitet",
    layout: "Layout",
    standard: "Standard",
    compact: "Kompakt",
    cardTitle: "Korttitel",
    hideFields: "Dölj fält",
    showPrice: "Visa pris",
    showConnectorDetails: "Visa kontaktdetaljer",
    showStationDetails: "Visa stationsdetaljer",
    showUpdateTimes: "Visa uppdateringstider",
    evEstimates: "Laddningsuppskattning för elbil",
    enableEvEstimates: "Visa uppskattningar för elbil",
    socEntity: "Sensor för batterinivå (%)",
    capacityEntity: "Sensor för användbar batterikapacitet (kWh)",
    powerEntity: "Sensor för elbilens laddeffekt (kW)",
    sensorAttribute: "Sensorattribut (valfritt)",
    targetSoc: "Laddningsmål (%)",
    assumedPower: "Antagen laddeffekt (kW)",
    energyNeeded: "Energi till batteriet",
    estimatedCost: "Uppskattad kostnad till laddningsmålet",
    estimatedDuration: "Uppskattad tid till laddningsmålet",
    evEstimateNote: "Uppskattningen exkluderar laddningsförluster, varierande laddeffekt och ej rapporterade tariffkomponenter.",
    entityMissing: "Den konfigurerade entiteten hittades inte.",
    wrongEntity: "Välj en status- eller rekommendationssensor från CurbCharge.",
    invalidConfig: "Använd en status- eller rekommendationssensor från CurbCharge och visningsalternativ som stöds.",
  },
  fi: {
    available: "Vapaa",
    favorite: "Suosikki",
    selected: "Valittu",
    topNearby: "Parhaat lähellä olevat laturit",
    recommendations: "Suositukset",
    rank: "Sija",
    score: "Pisteet",
    reason: "Peruste",
    bestNearbyRanking: "Järjestetty Paras lähellä olevan mukaan",
    noneAvailable: "Ei yhtään vapaata",
    noCurrentStations: "Ei ajantasaisia asemia",
    unknownAvailability: "Saatavuus tuntematon",
    noComparablePrice: "Hinta ei vertailukelpoinen",
    noPositivePower: "Ei positiivista tehoa",
    noKnownDistance: "Etäisyys tuntematon",
    unknown: "Tuntematon",
    unavailable: "Ei käytettävissä",
    availableConnectors: "Vapaat liittimet",
    occupiedConnectors: "Varatut liittimet",
    unavailableConnectors: "Ei käytettävissä olevat liittimet",
    totalConnectors: "Liittimet yhteensä",
    maximumPower: "Enimmäislatausteho",
    price: "Hinta per kWh",
    operator: "Operaattori",
    address: "Osoite",
    distance: "Etäisyys",
    providerOpenStatus: "Palveluntarjoajan ilmoittama avoinna-tila",
    open: "Avoinna",
    closed: "Suljettu",
    providerUpdated: "Palveluntarjoajan asemapäivitys",
    providerTimestampSource: "Palveluntarjoajan aikaleiman lähde",
    providerDataAge: "Palveluntarjoajan tietueen ikä",
    integrationUpdated: "Viimeisin onnistunut päivitys",
    details: "Lisätiedot",
    personalized: "Personoitu",
    preset: "Järjestyksen esiasetus",
    balancedPreset: "Tasapainotettu",
    lowestPricePreset: "Alin hinta",
    shortestDistancePreset: "Lyhin etäisyys",
    highestAvailabilityPreset: "Eniten vapaita liittimiä",
    highestPowerPreset: "Suurin liitinteho",
    fullMatch: "Täysi osuma",
    partialMatch: "Osittainen osuma",
    fallback: "Vararatkaisu",
    noMatch: "Ei osumaa",
    noPersonalizedMatch: "Ei personoitua osumaa",
    matchedConnectorGroup: "Sopiva liitinryhmä",
    fallbackReason: "Vararatkaisun syy",
    fallbackPreferredOperator: "Ensisijainen operaattori ei täsmännyt",
    fallbackUnknownConnectorType: "Liitintyyppi tuntematon",
    fallbackUnknownPower: "Teho tuntematon",
    fallbackUnknownOperator: "Operaattori tuntematon",
    preferenceMismatch: "Mieltymys ei täsmännyt",
    navigate: "Navigoi",
    googleDirections: "Google Mapsin reittiohjeet",
    appleDirections: "Apple Mapsin reittiohjeet",
    wazeNavigation: "Navigointi Wazella",
    openMap: "Avaa karttasijainti (ei reittiohjeita)",
    entity: "Asemaentiteetti",
    layout: "Asettelu",
    standard: "Vakio",
    compact: "Tiivis",
    cardTitle: "Kortin otsikko",
    hideFields: "Piilota kenttiä",
    showPrice: "Näytä hinta",
    showConnectorDetails: "Näytä liitintiedot",
    showStationDetails: "Näytä asematiedot",
    showUpdateTimes: "Näytä päivitysajat",
    evEstimates: "Sähköauton latausarvio",
    enableEvEstimates: "Näytä sähköauton arviot",
    socEntity: "Akun varaustason anturi (%)",
    capacityEntity: "Akun käytettävissä olevan kapasiteetin anturi (kWh)",
    powerEntity: "Sähköauton lataustehon anturi (kW)",
    sensorAttribute: "Anturin attribuutti (valinnainen)",
    targetSoc: "Tavoitevaraustaso (%)",
    assumedPower: "Oletettu latausteho (kW)",
    energyNeeded: "Akkuun tarvittava energia",
    estimatedCost: "Arvioitu hinta varaustavoitteeseen",
    estimatedDuration: "Arvioitu aika varaustavoitteeseen",
    evEstimateNote: "Arvio ei sisällä lataushäviöitä, vaihtelevaa lataustehoa eikä ilmoittamattomia tariffikomponentteja.",
    entityMissing: "Määritettyä entiteettiä ei löytynyt.",
    wrongEntity: "Valitse CurbChargen tila- tai suositussensori.",
    invalidConfig: "Käytä CurbChargen tila- tai suositussensoria ja tuettuja näyttöasetuksia.",
  },
  it: {
    available: "Disponibile",
    favorite: "Preferito",
    selected: "Selezionato",
    topNearby: "Migliori colonnine nelle vicinanze",
    recommendations: "Raccomandazioni",
    rank: "Posizione",
    score: "Punteggio",
    reason: "Motivo",
    bestNearbyRanking: "Ordinato per Migliore nelle vicinanze",
    noneAvailable: "Nessuna disponibile",
    noCurrentStations: "Nessuna stazione attuale",
    unknownAvailability: "Disponibilità sconosciuta",
    noComparablePrice: "Prezzo non confrontabile",
    noPositivePower: "Nessuna potenza positiva",
    noKnownDistance: "Distanza sconosciuta",
    unknown: "Sconosciuto",
    unavailable: "Non disponibile",
    availableConnectors: "Connettori disponibili",
    occupiedConnectors: "Connettori occupati",
    unavailableConnectors: "Connettori non disponibili",
    totalConnectors: "Connettori totali",
    maximumPower: "Potenza massima di ricarica",
    price: "Prezzo per kWh",
    operator: "Operatore",
    address: "Indirizzo",
    distance: "Distanza",
    providerOpenStatus: "Stato di apertura segnalato dal fornitore",
    open: "Aperto",
    closed: "Chiuso",
    providerUpdated: "Aggiornamento stazione del fornitore",
    providerTimestampSource: "Fonte del timestamp del fornitore",
    providerDataAge: "Età dei dati del fornitore",
    integrationUpdated: "Ultimo aggiornamento riuscito",
    details: "Maggiori dettagli",
    personalized: "Personalizzato",
    preset: "Preset di classificazione",
    balancedPreset: "Bilanciato",
    lowestPricePreset: "Prezzo più basso",
    shortestDistancePreset: "Distanza più breve",
    highestAvailabilityPreset: "Più connettori disponibili",
    highestPowerPreset: "Potenza del connettore più alta",
    fullMatch: "Corrispondenza completa",
    partialMatch: "Corrispondenza parziale",
    fallback: "Fallback",
    noMatch: "Nessuna corrispondenza",
    noPersonalizedMatch: "Nessuna corrispondenza personalizzata",
    matchedConnectorGroup: "Gruppo di connettori corrispondente",
    fallbackReason: "Motivo del fallback",
    fallbackPreferredOperator: "Operatore preferito non trovato",
    fallbackUnknownConnectorType: "Tipo di connettore sconosciuto",
    fallbackUnknownPower: "Potenza sconosciuta",
    fallbackUnknownOperator: "Operatore sconosciuto",
    preferenceMismatch: "Preferenza non corrispondente",
    navigate: "Naviga",
    googleDirections: "Indicazioni Google Maps",
    appleDirections: "Indicazioni Apple Maps",
    wazeNavigation: "Navigazione Waze",
    openMap: "Apri posizione sulla mappa (senza indicazioni)",
    entity: "Entità stazione",
    layout: "Layout",
    standard: "Standard",
    compact: "Compatto",
    cardTitle: "Titolo della scheda",
    hideFields: "Nascondi campi",
    showPrice: "Mostra prezzo",
    showConnectorDetails: "Mostra dettagli connettori",
    showStationDetails: "Mostra dettagli stazione",
    showUpdateTimes: "Mostra orari di aggiornamento",
    evEstimates: "Stima di ricarica dell'auto elettrica",
    enableEvEstimates: "Mostra le stime dell'auto elettrica",
    socEntity: "Sensore del livello della batteria (%)",
    capacityEntity: "Sensore della capacità utilizzabile della batteria (kWh)",
    powerEntity: "Sensore della potenza di ricarica dell'auto (kW)",
    sensorAttribute: "Attributo del sensore (facoltativo)",
    targetSoc: "Livello di carica obiettivo (%)",
    assumedPower: "Potenza di ricarica ipotizzata (kW)",
    energyNeeded: "Energia necessaria per la batteria",
    estimatedCost: "Costo stimato fino all'obiettivo",
    estimatedDuration: "Tempo stimato fino all'obiettivo",
    evEstimateNote: "La stima esclude le perdite di ricarica, le variazioni di potenza e le componenti tariffarie non comunicate.",
    entityMissing: "L'entità configurata non è stata trovata.",
    wrongEntity: "Scegli un sensore di stato o di raccomandazione CurbCharge.",
    invalidConfig: "Usa un sensore di stato o di raccomandazione CurbCharge e opzioni di visualizzazione supportate.",
  },
  lv: {
    available: "Pieejams",
    favorite: "Izlase",
    selected: "Atlasīts",
    topNearby: "Labākās uzlādes stacijas tuvumā",
    recommendations: "Ieteikumi",
    rank: "Vieta",
    score: "Vērtējums",
    reason: "Iemesls",
    bestNearbyRanking: "Sarindots pēc ieteikuma “Labākā uzlādes stacija tuvumā”",
    noneAvailable: "Nav pieejamu",
    noCurrentStations: "Nav aktuālu staciju",
    unknownAvailability: "Pieejamība nav zināma",
    noComparablePrice: "Cena nav salīdzināma",
    noPositivePower: "Nav pozitīvas jaudas",
    noKnownDistance: "Attālums nav zināms",
    unknown: "Nezināms",
    unavailable: "Nav pieejams",
    availableConnectors: "Pieejamie savienotāji",
    occupiedConnectors: "Aizņemtie savienotāji",
    unavailableConnectors: "Nepieejamie savienotāji",
    totalConnectors: "Kopējais savienotāju skaits",
    maximumPower: "Maksimālā uzlādes jauda",
    price: "Cena par kWh",
    operator: "Operators",
    address: "Adrese",
    distance: "Attālums",
    providerOpenStatus: "Pakalpojumu sniedzēja norādītais atvēršanas statuss",
    open: "Atvērts",
    closed: "Slēgts",
    providerUpdated: "Stacijas atjauninājums no pakalpojumu sniedzēja",
    providerTimestampSource: "Pakalpojumu sniedzēja laikspiedola avots",
    providerDataAge: "Pakalpojumu sniedzēja datu vecums",
    integrationUpdated: "Pēdējais veiksmīgais atjauninājums",
    details: "Papildinformācija",
    personalized: "Personalizēts",
    preset: "Ieteikumu secība",
    balancedPreset: "Līdzsvarots",
    lowestPricePreset: "Zemākā cena",
    shortestDistancePreset: "Mazākais attālums",
    highestAvailabilityPreset: "Visvairāk pieejamo savienotāju",
    highestPowerPreset: "Lielākā savienotāja jauda",
    fullMatch: "Pilna atbilstība",
    partialMatch: "Daļēja atbilstība",
    fallback: "Atkāpe",
    noMatch: "Nav atbilstības",
    noPersonalizedMatch: "Nav personalizētas atbilstības",
    matchedConnectorGroup: "Atbilstoša savienotāju grupa",
    fallbackReason: "Atkāpes iemesls",
    fallbackPreferredOperator: "Vēlamais operators neatbilst",
    fallbackUnknownConnectorType: "Savienotāja veids nav zināms",
    fallbackUnknownPower: "Jauda nav zināma",
    fallbackUnknownOperator: "Operators nav zināms",
    preferenceMismatch: "Priekšroka neatbilst",
    navigate: "Navigēt",
    googleDirections: "Maršruts Google Maps",
    appleDirections: "Maršruts Apple Maps",
    wazeNavigation: "Navigācija ar Waze",
    openMap: "Atvērt atrašanās vietu kartē (bez maršruta)",
    entity: "Stacijas entītija",
    layout: "Izkārtojums",
    standard: "Standarta",
    compact: "Kompakts",
    cardTitle: "Kartītes virsraksts",
    hideFields: "Slēpt laukus",
    showPrice: "Rādīt cenu",
    showConnectorDetails: "Rādīt savienotāju informāciju",
    showStationDetails: "Rādīt stacijas informāciju",
    showUpdateTimes: "Rādīt atjauninājumu laikus",
    evEstimates: "Elektroauto uzlādes aprēķins",
    enableEvEstimates: "Rādīt elektroauto aprēķinus",
    socEntity: "Akumulatora uzlādes līmeņa sensors (%)",
    capacityEntity: "Izmantojamās akumulatora ietilpības sensors (kWh)",
    powerEntity: "Elektroauto uzlādes jaudas sensors (kW)",
    sensorAttribute: "Sensora atribūts (neobligāts)",
    targetSoc: "Mērķa uzlādes līmenis (%)",
    assumedPower: "Pieņemtā uzlādes jauda (kW)",
    energyNeeded: "Akumulatoram nepieciešamā enerģija",
    estimatedCost: "Aprēķinātās izmaksas līdz mērķim",
    estimatedDuration: "Aprēķinātais laiks līdz mērķim",
    evEstimateNote: "Aprēķinā nav iekļauti uzlādes zudumi, jaudas izmaiņas un neziņoti tarifa komponenti.",
    entityMissing: "Konfigurētā entītija nav atrasta.",
    wrongEntity: "Izvēlieties CurbCharge statusa vai ieteikumu sensoru.",
    invalidConfig:
      "Izmantojiet CurbCharge statusa vai ieteikumu sensoru un atbalstītas attēlošanas opcijas.",
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

function formatProviderDataAge(value, hass, unknown) {
  return Number.isInteger(value) && value >= 0
    ? `${formatCount(value, hass, unknown)} s`
    : unknown;
}

function formatTimestampSource(value, unknown) {
  return typeof value === "string" && value.trim()
    ? value.trim().replaceAll("_", " ")
    : unknown;
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
  const hideFields = options.hide_fields || [];
  const visibleRows = filterHiddenRows(rows, hideFields, text);
  const personalizationRows = filterHiddenRows(
    formatPersonalizationRows(recommendation.personalization, hass, text),
    hideFields,
    text,
  );
  return personalizationRows.length
    ? {
        name,
        rows: visibleRows,
        personalizationRows,
        personalizationLabel: text.personalized,
        personalizationKey: String(
          recommendation.provider_station_id ||
            recommendation.station_id ||
            name,
        ),
      }
    : { name, rows: visibleRows };
}

function formatPersonalizationRows(personalization, hass, text) {
  if (
    !personalization ||
    typeof personalization !== "object" ||
    personalization.enabled !== true
  ) {
    return [];
  }

  const statuses = {
    full_match: text.fullMatch,
    partial_match: text.partialMatch,
    fallback: text.fallback,
    no_match: text.noMatch,
    not_evaluated: text.unknown,
    unavailable: text.unavailable,
  };
  const presets = {
    balanced: text.balancedPreset,
    lowest_price: text.lowestPricePreset,
    shortest_distance: text.shortestDistancePreset,
    highest_availability: text.highestAvailabilityPreset,
    highest_power: text.highestPowerPreset,
  };
  const rows = [
    [
      text.personalized,
      statuses[personalization.match_status] || text.unknown,
    ],
    [text.preset, presets[personalization.preset] || text.unknown],
  ];
  const group = personalization.matched_connector_group;
  if (group && typeof group === "object") {
    const values = [];
    if (typeof group.connector_type_id === "string") {
      values.push(group.connector_type_id.replaceAll("_", " "));
    }
    if (typeof group.max_power_kw === "number" && Number.isFinite(group.max_power_kw)) {
      values.push(
        `${formatNumber(group.max_power_kw, hass, text.unknown)} kW`,
      );
    }
    rows.push([text.matchedConnectorGroup, values.join(", ")]);
  }

  if (personalization.fallback_used === true) {
    const fallbackLabels = {
      preferred_operator_not_matched: text.fallbackPreferredOperator,
      unknown_connector_type: text.fallbackUnknownConnectorType,
      unknown_power: text.fallbackUnknownPower,
      unknown_operator: text.fallbackUnknownOperator,
    };
    const reasons = Array.isArray(personalization.fallback_reasons)
      ? personalization.fallback_reasons.map(
          (reason) => fallbackLabels[reason] || text.unknown,
        )
      : [];
    rows.push([
      text.fallbackReason,
      reasons.length > 0 ? reasons.join(", ") : text.fallback,
    ]);
  } else if (
    Array.isArray(personalization.preference_mismatches) &&
    personalization.preference_mismatches.includes("preferred_operator")
  ) {
    rows.push([text.preferenceMismatch, text.fallbackPreferredOperator]);
  }

  return rows;
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
    BOOLEAN_OPTIONS.some((option) => typeof normalized[option] !== "boolean") ||
    typeof normalized.enable_ev_estimates !== "boolean" ||
    !Number.isFinite(normalized.ev_target_soc) ||
    normalized.ev_target_soc < 0 ||
    normalized.ev_target_soc > 100 ||
    (normalized.title !== undefined && typeof normalized.title !== "string") ||
    [
      normalized.ev_soc_entity,
      normalized.ev_capacity_entity,
      normalized.ev_power_entity,
    ].some(
      (entity) =>
        entity !== undefined &&
        entity !== null &&
        entity !== "" &&
        (typeof entity !== "string" || !entity.startsWith("sensor.")),
    ) ||
    [
      normalized.ev_soc_attribute,
      normalized.ev_capacity_attribute,
      normalized.ev_power_attribute,
    ].some(
      (attribute) =>
        attribute !== undefined && typeof attribute !== "string",
    ) ||
    (normalized.ev_assumed_power_kw !== undefined &&
      normalized.ev_assumed_power_kw !== null &&
      (typeof normalized.ev_assumed_power_kw !== "number" ||
        !Number.isFinite(normalized.ev_assumed_power_kw) ||
        normalized.ev_assumed_power_kw <= 0)) ||
    (normalized.hide_fields !== undefined &&
      (!Array.isArray(normalized.hide_fields) ||
        normalized.hide_fields.some(
          (field) =>
            typeof field !== "string" ||
            !Object.hasOwn(FIELD_LABELS, field),
        )))
  ) {
    throw new Error(TEXT.en.invalidConfig);
  }
  return normalized;
}

function filterHiddenRows(rows, hideFields, text) {
  const hiddenLabels = new Set(
    hideFields.map((field) => text[FIELD_LABELS[field]]),
  );
  return rows.filter(([label]) => !hiddenLabels.has(label));
}

function readSensorValue(hass, entityId, attribute, expectedUnit) {
  if (typeof entityId !== "string" || !entityId) {
    return null;
  }
  const entity = hass?.states?.[entityId];
  const attributes = entity?.attributes || {};
  if (
    !entity ||
    entity.state === "unknown" ||
    entity.state === "unavailable" ||
    attributes.is_stale === true ||
    attributes.restored === true ||
    (!attribute && attributes.unit_of_measurement !== expectedUnit)
  ) {
    return null;
  }
  const raw = attribute ? attributes[attribute] : entity.state;
  if (
    (typeof raw !== "number" && typeof raw !== "string") ||
    (typeof raw === "string" && !raw.trim())
  ) {
    return null;
  }
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

function evEstimateRows(attributes, hass, options, text) {
  if (!options.enable_ev_estimates) {
    return [];
  }
  const stateOfCharge = readSensorValue(
    hass,
    options.ev_soc_entity,
    options.ev_soc_attribute,
    "%",
  );
  const capacity = readSensorValue(
    hass,
    options.ev_capacity_entity,
    options.ev_capacity_attribute,
    "kWh",
  );
  if (
    stateOfCharge === null ||
    stateOfCharge < 0 ||
    stateOfCharge > 100 ||
    capacity === null ||
    capacity <= 0
  ) {
    return [];
  }

  const energyNeeded =
    (capacity * Math.max(0, options.ev_target_soc - stateOfCharge)) / 100;
  const rows = [
    [
      text.energyNeeded,
      `${formatNumber(energyNeeded, hass, text.unknown)} kWh`,
    ],
  ];
  const fee = attributes.session_fee;
  const perMinutePrice = attributes.price_per_minute;
  if (
    attributes.tariff_status === "available" &&
    typeof attributes.price_per_kwh === "number" &&
    Number.isFinite(attributes.price_per_kwh) &&
    attributes.price_per_kwh >= 0 &&
    typeof attributes.currency === "string" &&
    /^[A-Z]{3}$/.test(attributes.currency) &&
    Object.hasOwn(attributes, "session_fee") &&
    (fee === null ||
      (typeof fee === "number" && Number.isFinite(fee) && fee >= 0)) &&
    Object.hasOwn(attributes, "price_per_minute") &&
    (perMinutePrice === null ||
      (typeof perMinutePrice === "number" &&
        Number.isFinite(perMinutePrice) &&
        perMinutePrice === 0))
  ) {
    const cost =
      energyNeeded === 0
        ? 0
        : energyNeeded * attributes.price_per_kwh + (fee ?? 0);
    rows.push([
      text.estimatedCost,
      new Intl.NumberFormat(
        hass?.locale?.language || hass?.language || "en",
        { style: "currency", currency: attributes.currency },
      ).format(cost),
    ]);
  }

  const power = options.ev_power_entity
    ? readSensorValue(
        hass,
        options.ev_power_entity,
        options.ev_power_attribute,
        "kW",
      )
    : options.ev_assumed_power_kw;
  if (typeof power === "number" && Number.isFinite(power) && power > 0) {
    rows.push([
      text.estimatedDuration,
      `${formatNumber(energyNeeded / power, hass, text.unknown)} h`,
    ]);
  }
  return rows;
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
    no_personalized_match: text.noPersonalizedMatch,
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
      : entity.state === "unknown" &&
          typeof attributes.provider_station_id === "string"
        ? "unknown_availability"
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
  const hideFields = options.hide_fields || [];
  const estimates =
    !hasRecommendationList && !unavailable
      ? evEstimateRows(values, hass, options, text)
      : [];
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
        text.providerTimestampSource,
        formatTimestampSource(values.provider_timestamp_source, text.unknown),
      ],
      [
        text.providerDataAge,
        formatProviderDataAge(
          values.provider_data_age_seconds,
          hass,
          text.unknown,
        ),
      ],
      [
        text.integrationUpdated,
        formatTimestamp(values.last_successful_update, hass, text.unknown),
      ],
    );
  }
  if (!hasRecommendationList) {
    detailsRows.push(
      ...formatPersonalizationRows(values.personalization, hass, text),
    );
  }

  return {
    title:
      (typeof options.title === "string" && options.title.trim()) ||
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
    evEstimateLabel: text.evEstimates,
    evEstimateNote: text.evEstimateNote,
    evEstimateRows: estimates,
    primaryRows: filterHiddenRows(primaryRows, hideFields, text),
    detailsRows: filterHiddenRows(detailsRows, hideFields, text),
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
    {
      name: "title",
      selector: { text: {} },
    },
    {
      name: "hide_fields",
      selector: {
        select: {
          multiple: true,
          options: Object.entries(FIELD_LABELS).map(([value, labelKey]) => ({
            value,
            label: text[labelKey],
          })),
        },
      },
    },
    ...BOOLEAN_OPTIONS.map((name) => ({
      name,
      selector: { boolean: {} },
    })),
    {
      name: "enable_ev_estimates",
      selector: { boolean: {} },
    },
    {
      name: "ev_soc_entity",
      selector: { entity: { domain: "sensor" } },
    },
    {
      name: "ev_soc_attribute",
      selector: { text: {} },
    },
    {
      name: "ev_capacity_entity",
      selector: { entity: { domain: "sensor" } },
    },
    {
      name: "ev_capacity_attribute",
      selector: { text: {} },
    },
    {
      name: "ev_power_entity",
      selector: { entity: { domain: "sensor" } },
    },
    {
      name: "ev_power_attribute",
      selector: { text: {} },
    },
    {
      name: "ev_target_soc",
      selector: { number: { min: 0, max: 100, step: 1, mode: "box" } },
    },
    {
      name: "ev_assumed_power_kw",
      selector: { number: { min: 0.1, step: 0.1, mode: "box" } },
    },
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

    const entity = this._hass.states[this._config.entity];
    const vehicleEntities = this._config.enable_ev_estimates
      ? [
          this._config.ev_soc_entity,
          this._config.ev_capacity_entity,
          this._config.ev_power_entity,
        ]
          .filter(Boolean)
          .map((entityId) => this._hass.states[entityId])
      : [];
    const language =
      this._hass.locale?.language || this._hass.language || "en";
    const sameVehicleEntities =
      this._lastRenderedVehicleEntities?.length === vehicleEntities.length &&
      vehicleEntities.every(
        (vehicleEntity, index) =>
          vehicleEntity === this._lastRenderedVehicleEntities[index],
      );
    if (
      this._lastRenderedConfig === this._config &&
      this._lastRenderedEntity === entity &&
      sameVehicleEntities &&
      this._lastRenderedLanguage === language
    ) {
      return;
    }

    const openDetails = new Map(
      [...this.shadowRoot.querySelectorAll("details")].map((details) => [
        details.dataset.focusKey,
        details.open,
      ]),
    );
    const focusedKey = this.shadowRoot.activeElement?.dataset.focusKey;
    const data = buildCardData(
      entity,
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
      this._lastRenderedConfig = this._config;
      this._lastRenderedEntity = entity;
      this._lastRenderedVehicleEntities = vehicleEntities;
      this._lastRenderedLanguage = language;
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

    if (data.evEstimateRows.length > 0) {
      const section = document.createElement("section");
      section.className = "ev-estimates";
      const heading = document.createElement("h3");
      heading.className = "recommendations-heading";
      heading.textContent = data.evEstimateLabel;
      const grid = document.createElement("div");
      grid.className = "details-grid";
      for (const row of data.evEstimateRows) {
        grid.append(createMetric(row));
      }
      const note = document.createElement("p");
      note.textContent = data.evEstimateNote;
      section.append(heading, grid, note);
      card.append(section);
    }

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
        if (recommendation.personalizationRows?.length) {
          const personalizationDetails = document.createElement("details");
          personalizationDetails.className = "personalization-details";
          personalizationDetails.dataset.focusKey =
            `personalization-${recommendation.personalizationKey}`;
          personalizationDetails.open =
            openDetails.get(personalizationDetails.dataset.focusKey) ?? false;
          const summary = document.createElement("summary");
          summary.dataset.focusKey =
            `${personalizationDetails.dataset.focusKey}-summary`;
          summary.textContent = recommendation.personalizationLabel;
          const detailsGrid = document.createElement("div");
          detailsGrid.className = "details-grid";
          for (const row of recommendation.personalizationRows) {
            detailsGrid.append(createMetric(row));
          }
          personalizationDetails.append(summary, detailsGrid);
          item.append(personalizationDetails);
        }
        list.append(item);
      }
      section.append(heading, list);
      card.append(section);
    }

    if (data.detailsRows.length > 0) {
      const details = document.createElement("details");
      details.className = "details";
      details.dataset.focusKey = "details";
      details.open = openDetails.get("details") ?? false;
      const summary = document.createElement("summary");
      summary.dataset.focusKey = "details-summary";
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
      navigation.dataset.focusKey = "navigation";
      navigation.open = openDetails.get("navigation") ?? false;
      const summary = document.createElement("summary");
      summary.dataset.focusKey = "navigation-summary";
      summary.textContent = data.navigationLabel;
      const list = document.createElement("ul");
      list.className = "navigation-links";
      for (const [index, item] of data.navigationLinks.entries()) {
        const row = document.createElement("li");
        const link = document.createElement("a");
        link.dataset.focusKey = `navigation-link-${index}`;
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

    if (focusedKey) {
      [...this.shadowRoot.querySelectorAll("[data-focus-key]")]
        .find((element) => element.dataset.focusKey === focusedKey)
        ?.focus();
    }
    this._lastRenderedConfig = this._config;
    this._lastRenderedEntity = entity;
    this._lastRenderedVehicleEntities = vehicleEntities;
    this._lastRenderedLanguage = language;
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
