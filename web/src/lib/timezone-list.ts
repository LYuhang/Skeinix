/**
 * Curated, readable list of common IANA timezones for the Settings selector.
 *
 * Rationale (non-technical, GLOBAL audience): `Intl.supportedValuesOf('timeZone')`
 * returns ~400 raw IANA ids — overwhelming and unfriendly. We hand-pick a few
 * dozen well-known zones grouped by region, with human labels. The browser's
 * detected zone is surfaced separately at the TOP of the dropdown by the
 * Settings card so the user's "obvious" choice is one click away.
 */
export interface TimezoneOption {
  /** IANA id, e.g. `'Asia/Shanghai'`. */
  value: string;
  /** Human label, e.g. `'Shanghai / Beijing'`. */
  label: string;
}

export interface TimezoneGroup {
  /** Region label, e.g. `'Asia'`. */
  region: string;
  zones: TimezoneOption[];
}

/**
 * Grouped curated zones. Labels are intentionally city/region-first and
 * locale-neutral (Settings can prepend the live UTC offset at render time if
 * desired). Kept deliberately short for scannability.
 */
export const TIMEZONE_GROUPS: TimezoneGroup[] = [
  {
    region: 'Universal',
    zones: [{ value: 'UTC', label: 'UTC (Coordinated Universal Time)' }],
  },
  {
    region: 'Asia',
    zones: [
      { value: 'Asia/Shanghai', label: 'Shanghai / Beijing' },
      { value: 'Asia/Hong_Kong', label: 'Hong Kong' },
      { value: 'Asia/Taipei', label: 'Taipei' },
      { value: 'Asia/Singapore', label: 'Singapore' },
      { value: 'Asia/Tokyo', label: 'Tokyo' },
      { value: 'Asia/Seoul', label: 'Seoul' },
      { value: 'Asia/Jakarta', label: 'Jakarta' },
      { value: 'Asia/Bangkok', label: 'Bangkok' },
      { value: 'Asia/Kolkata', label: 'Mumbai / New Delhi' },
      { value: 'Asia/Dubai', label: 'Dubai' },
    ],
  },
  {
    region: 'Europe',
    zones: [
      { value: 'Europe/London', label: 'London' },
      { value: 'Europe/Paris', label: 'Paris' },
      { value: 'Europe/Berlin', label: 'Berlin' },
      { value: 'Europe/Madrid', label: 'Madrid' },
      { value: 'Europe/Moscow', label: 'Moscow' },
    ],
  },
  {
    region: 'Americas',
    zones: [
      { value: 'America/New_York', label: 'New York (Eastern)' },
      { value: 'America/Chicago', label: 'Chicago (Central)' },
      { value: 'America/Denver', label: 'Denver (Mountain)' },
      { value: 'America/Los_Angeles', label: 'Los Angeles (Pacific)' },
      { value: 'America/Sao_Paulo', label: 'São Paulo' },
      { value: 'America/Mexico_City', label: 'Mexico City' },
    ],
  },
  {
    region: 'Oceania',
    zones: [
      { value: 'Australia/Sydney', label: 'Sydney' },
      { value: 'Australia/Perth', label: 'Perth' },
      { value: 'Pacific/Auckland', label: 'Auckland' },
    ],
  },
];

/** Flat list of all curated zone ids (for membership checks). */
export const CURATED_ZONE_IDS: string[] = TIMEZONE_GROUPS.flatMap((g) =>
  g.zones.map((z) => z.value),
);
