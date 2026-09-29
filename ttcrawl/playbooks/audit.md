# audit: the live site's health, weekly
Broken internal links and images, redirect chains, missing titles, descriptions and h1s, duplicate titles, heavy images, sitemap drift, JSON-LD that does not parse, and the static accessibility checks. A report only: it never touches the site's pages folder.

## Run
```
tt-crawl audit https://theirdomain.com                    # report: raw/audit/<host>/<date>.md
tt-crawl audit http://localhost:3000 --no-register        # a working copy; not recorded as the host's latest
```
Schedule the first as a weekly job; it exits 1 when something needs fixing, which is what alerts the owner. `raw/audit/_latest.json` names the newest report per host and whether it passed.

## Then
Fix what is in the site's control, rebuild, redeploy, and re-run the audit to show it clean.
