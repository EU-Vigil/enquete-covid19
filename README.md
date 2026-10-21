# EU-VIGIL — COVID-19 pharmacovigilance

## Scope

Statistical investigation of spontaneous adverse event reports
associated with COVID-19 vaccines in the EU-VIGIL dataset.

- Reference universe: 13,483,992 reports
- COVID-19 vaccine reports: 2,322,888
- 9 vaccine brands and 22 formulations
- 81,682 vaccine–MedDRA pairs associated with at least one
  reported seriousness criterion
- 80,776 estimable pairs
- 906 pairs with a zero cell, not estimated in this release

## Methods

- Reporting Odds Ratio (ROR)
- Proportional Reporting Ratio (PRR)
- Bayesian Confidence Propagation Neural Network (BCPNN)
- Gamma Poisson Shrinker (GPS)

The GPS hyperparameters are preserved in
`reference/meta.json`.

## Interpretation

A signal of a suspected adverse reaction is not proof of
causation. ROR and PRR compare reporting distributions;
they are not individual risk ratios or incidence estimates.

Counts with a death or hospitalisation seriousness criterion
must not be interpreted as confirmed vaccine-caused deaths
or hospitalisations.

The overall COVID-19 group, brand-level groups and
formulation-level groups are overlapping analytical levels.
Their signal counts must not be added together.

## Reproducibility

`code/calcul_signaux.py` is the original calculation script.
It refers to the EU-VIGIL server directory layout and
requires the corresponding aggregated input tables and
the complete reference event index.

The CSV and the reference metadata are provided here.
The raw individual case database is not included.

Additional reproducibility materials will be added after
verification of their redistribution and privacy conditions.

## Source

https://eu-vigil.com/enquetes/covid-19/

Contact : contact@eu-vigil.com

## Licence / License
- Code (`code/`, `scripts/`) : Apache License 2.0 — voir `LICENSE`.
- Données (`data/`, `reference/`) : Creative Commons Attribution 4.0 (CC BY 4.0). Réutilisation libre en citant la source : « EU-Vigil, données EudraVigilance ».

Copyright © 2026 EU-Vigil.
