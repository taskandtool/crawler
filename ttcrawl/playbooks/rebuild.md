# rebuild: bring an old site over whole
For a migration: every page with its text verbatim, every picture in the content once at its largest, an inventory of every old URL. A survey first says how big it is.

## Run
```
tt-crawl survey https://theirsite.com            # first, always: the size and the templates; read templates.md
tt-crawl pages https://theirsite.com --max-pages N   # N from the URLs the survey found (default 1000)
tt-crawl docs
tt-crawl import                                  # a WordPress site ("wordpress": true in raw/site/_sites.json): every post and page through its own API
```
A long crawl saves its place every ten pages: if it stops, run the same command with `--resume`. Nothing is read twice and pictures already fetched are reused. `tt-crawl add URL` reads one more page.

## Read
`_index/inventory.md` (one row per old URL: the list the new site map and the redirects are planned from), `_index/templates.md`, `_index/furniture.json` (the old nav and footer), `pages/`, `images/` with `_index/media.json`, `_index/facts.json`.

## At launch
`tt-crawl playbook launch`: every old URL checked on the new site.
