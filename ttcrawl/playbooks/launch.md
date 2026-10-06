# launch: check the rebuilt site against the old one
Every URL the old site had, requested on the new one: a verdict per row (ok, missing, redirect chain, noindex, error), the new sitemap diffed against the old inventory, the home page's structured data.

## Run
```
tt-crawl check http://localhost:3000                # before publishing: the site running on this machine
tt-crawl check https://theirdomain.com              # after the domain moves
```
The inventory defaults to the one site folder under raw/site; pass `--inventory` when there are several. Reports land in `raw/audit/<host>/launch-<date>.md`; `raw/audit/_launch.json` says whether the newest passed. Exit 1 means an old URL is missing or errors: fix it (a page or a redirect) and run it again. For two weeks after launch, run the weekly audit with the old inventory, `tt-crawl audit https://theirdomain.com --inventory raw/site/<host>/_index/inventory.json`, so the old URLs stay checked. The old site's folder is not crawled again: a crawl of the new site goes to another folder (`--out`).
