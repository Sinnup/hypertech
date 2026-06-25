# CNBV / Mexico Payments — Regulatory Notes (sample)

The Comisión Nacional Bancaria y de Valores (CNBV) regulates financial
institutions in Mexico, including payment aggregators and fintech entities under
the Ley para Regular las Instituciones de Tecnología Financiera (Ley Fintech).

Relevant points for a payment-terminal / TPV product:

- **IFPE / aggregator rules**: entities handling electronic payment funds must
  register and meet capital, AML (PLD), and reporting obligations.
- **AML / KYC (PLD)**: customer identification and transaction monitoring are
  mandatory; suspicious operations must be reported to the UIF.
- **Data protection**: personal data is governed by the LFPDPPP — obtain consent,
  limit retention, and honor ARCO rights.
- **CoDi / SPEI**: Banxico's instant-payment rails; QR-based CoDi is the
  preferred low-cost digital collection mechanism for small merchants.
- **Consumer protection (CONDUSEF)**: clear fee disclosure and dispute handling.

For a POC, document which of these apply at productionization time even if the
demo itself does not move real funds.
