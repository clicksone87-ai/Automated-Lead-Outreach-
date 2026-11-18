// The 16 draft languages (V2 Item 1). Keep in sync with SUPPORTED_LANGUAGES in
// backend/app/services/runtime_settings.py. "nb" is Norwegian (Bokmål).
// The list of COUNTRIES is not kept here: it comes from the backend (country_rules.yaml)
// through the settings API, so there is a single source of truth.
export const LANGUAGES = [
  "en", "de", "fr", "es", "it", "nl", "pt", "pl",
  "sv", "da", "nb", "fi", "cs", "ro", "el", "hu",
];