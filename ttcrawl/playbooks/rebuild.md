# rebuild: bring an old site over whole
For a migration: every page with its text verbatim, every picture in the content once at its largest, the inventory every old URL is ledgered against. Survey first when the site is big.

## Run
```
tt-crawl survey https://theirsite.com            # when it may have more than 100 pages; read templates.md
tt-crawl pages https://theirsite.com --max-pages N   # N from the survey's counts (default 1000)
tt-crawl docs
tt-crawl import --out raw/site/<host> --template post   # a collection through the platform's own API when it has one
```
A long crawl saves its place every ten pages: if it stops, run the same command with `--resume`. Nothing is read twice and pictures already fetched are reused. `tt-crawl add URL --out raw/site/<host>` reads one more page.

## Read
`_index/inventory.md` (one row per old URL: the ledger for the new site map and the redirects), `_index/templates.md`, `_index/furniture.json` (the old nav and footer), `pages/`, `images/` with `_index/media.json`, `_index/facts.json`.

## At launch
`tt-crawl check http://localhost:3000` against the old inventory, then `tt-crawl freeze raw/site/<host> --reason "the old site, at launch"`: the old site stays as it was, and later crawls of the new site go to another folder (`--out`).
