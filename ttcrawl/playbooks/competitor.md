# competitor: what another business says about itself
A competitor's, supplier's or directory's site, sampled: their services, prices and claims as they state them, for positioning and analysis, never as the owner's facts.

## Run
```
tt-crawl survey https://competitor.com --external
```
Into `raw/external/<host>/`. `tt-crawl add URL --out raw/external/<host>` for a page the survey left.

## Use
Their pages are what they say about themselves: cite `raw/external/<host>/pages/...` as theirs. Their facts describe the world, never the owner.
