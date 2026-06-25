# LACP / Data Protection — Compliance Notes (sample)

LACP (Latin America Cybersecurity & Data Protection) practices and the broader
data-protection regimes (e.g., LGPD in Brazil, LFPDPPP in Mexico) impose
requirements on any application that processes cardholder or personal data.

Core obligations for a payment application:

- **Lawful basis & consent**: collect only the data needed for the transaction;
  obtain explicit consent for any secondary use.
- **Data minimization & retention**: do not persist full PAN or track data;
  retain transaction records only as long as legally required.
- **Encryption**: encrypt sensitive data in transit (TLS 1.2+) and at rest.
- **Breach notification**: maintain an incident-response plan and notify the
  regulator and affected users within the mandated window.
- **Cross-border transfer**: ensure adequacy / contractual safeguards when data
  leaves the country of collection.

For a simulated POC with no real card data, these are forward-looking
requirements to capture in the compliance brief, not blockers for the demo.
