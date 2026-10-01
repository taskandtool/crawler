# import: bring a collection across
Posts, products, events: from the platform's own feed when it has one (WordPress REST, an RSS or Atom feed, Shopify's products.json), which gives exact dates, authors, categories and prices; HTML otherwise. Into the folder a crawl of the site already wrote.

## Run
A WordPress site, every post and page (no template to know; the default `--source auto` does the same):
```
tt-crawl import --source wp
```
One collection of any site (a survey names the templates):
```
tt-crawl survey https://theirsite.com                       # once, to know the templates
tt-crawl import --template post [--since 2023-01-01] [--limit 200]
```
The folder defaults to the one under raw/site; pass `--out raw/site/<host>` when there are several. `--source auto` (the default) asks every feed the site answers, WordPress's first, and takes each page from the first feed that carries it; a page none carries is read as HTML. `--source wp|rss|shopify|html` uses only that one. Pictures follow what the folder's crawl chose (`--images` to change it) and are fetched once each.

## Read
`pages/<name>.md` for each item: frontmatter with `date`, `author`, `categories` (and `price`, `vendor`, `tags` for products) and `fetcher` saying where it came from; the body verbatim. A WordPress import marks the folder `"wp_imported": true` in `raw/site/_sites.json`.

## Tell the owner
How many items, from which source, the date range, and anything the source did not have (a feed carries only recent posts; the rest came from their pages).
