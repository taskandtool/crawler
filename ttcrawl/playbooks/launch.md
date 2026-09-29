# launch: check the rebuilt site against the old one
Every URL the old site had, requested on the new one: a verdict per row (ok, missing, redirect chain, noindex, error), the new sitemap diffed against the old inventory, the home page's structured data.

## Run
```
tt-crawl check http://localhost:3000                # before publishing: the working copy
tt-crawl check https://theirdomain.com              # after the domain moves
tt-crawl freeze raw/site/<host> --reason "the old site, at launch"
```
The inventory defaults to the one site folder under raw/site; pass `--inventory` when there are several. Reports land in `raw/audit/<host>/launch-<date>.md`; `raw/audit/_launch.json` says whether the newest passed. For two weeks after launch, add `--inventory` to the weekly audit so the old URLs stay checked.
