Toolkit Converter (Freemium)

Features
- Free: basic metric conversions (length mm/cm/m/km, weight mg/g/kg, temperature C/F/K)
- Pro ($3/mo or $30/yr): imperial units, area, volume, time, extended weight
- Offline entitlement: 24h signed token enables offline Pro usage
- Payments: Dodo Payments checkout + license verification endpoints

Environment
- VITE_BACKEND_URL (frontend → backend)
- ENTITLEMENT_JWT_SECRET (backend signing key)
- DODO_API_KEY (backend → Dodo API)
- DODO_API_BASE (optional, defaults to https://api.dodopayments.com)

API
- POST /api/convert { value, from_unit, to_unit } → result (uses Bearer entitlement token if present)
- POST /api/license/verify { license_key, user_email? } → entitlement token (24h)
- POST /api/entitlement/refresh { entitlement_token } → refreshed token based on Dodo status
- GET /api/pricing → pricing info

Notes
- Replace placeholder Dodo API endpoints with exact paths from docs.
- 402 status indicates Pro is required.
