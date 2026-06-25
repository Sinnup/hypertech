# EMV Chip Payment — Compliance Essentials (sample)

EMV (Europay, Mastercard, Visa) is the global standard for chip-based card
payments. Terminals (TPV / POS) that accept chip cards must comply with EMV
Level 1 (electrical/physical) and Level 2 (application/kernel) certification.

Key requirements relevant to a payment terminal application:

- **Card authentication**: support offline (SDA/DDA/CDA) and online authentication.
- **Cardholder verification (CVM)**: PIN (online/offline), signature, or no-CVM
  for low-value contactless under the floor limit.
- **Transaction integrity**: the terminal must generate an Application
  Cryptogram (ARQC) for online authorization and validate the issuer response.
- **PCI DSS**: cardholder data (PAN, track data) must never be stored in clear
  text; the terminal must be PCI PTS approved for PIN entry.
- **Tokenization**: where possible, replace the PAN with a token for any data at
  rest or in transit beyond the authorization request.

For a demo/POC that only *simulates* card insertion, no real EMV kernel is
required, but the UX should still reflect the EMV flow: insert → read → online
authorize → approved/declined → receipt.
