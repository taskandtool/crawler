# import: bring one collection across
Posts, products, events: every page of one template, from the platform's own feed when it has one (WordPress REST, an RSS or Atom feed, Shopify's products.json), which gives exact dates, authors, categories and prices; HTML otherwise.

## Run
```
tt-crawl survey https://theirsite.com                       # once, to know the templates
tt-crawl import --out raw/site/<host> --template post [--since 2023-01-01] [--limit 200]
```
`--source auto|wp|rss|shopify|html` forces one source; auto tries them in that order and falls back to HTML page by page. Pictures in the content are fetched once each and linked from the pages.

## Read
`pages/<name>.md` for each item: frontmatter with `date`, `author`, `categories` (and `price`, `vendor`, `tags` for products) and `fetcher` saying where it came from; the body verbatim.

## Tell the owner
How many items, from which source, the date range, and anything the source did not have (a feed carries only recent posts; the rest came from their pages).
