# Domain rules

| Topic | Rule | Why |
|---|---|---|
| Money | Whole numbers in cents (10000 = $100.00) | Decimals like 0.1 + 0.2 give wrong answers in computers; whole cents never do |
| Currencies (v1) | USD, EUR, GBP only | All have 2 decimal places; others come later |
| Plus and minus | Money in = positive, money out = negative. Captures > 0, refunds < 0. Fees are stored as a positive cost. | Same rule in all three notebooks |
| Net | net = gross − fee | Can be checked on every row |
| Time | Store times in UTC. Each source says which timezone its "business date" uses. | A sale at 23:50 UTC can count as "tomorrow" for some companies |
| Fee | 2.9% of gross on captures only, rounded half-up to a cent | So we can spot a wrong fee |
| Tolerance | Up to 2 cents difference is accepted; money can arrive T+0 to T+2 days | Small rounding and normal delays are not mistakes |
| Tolerance total | Every accepted small difference is added up and reported | Many tiny "ignored" differences can hide a big leak |
| Run date | Every check runs "as of" a given date, never "now" | Same inputs always give the same answer |