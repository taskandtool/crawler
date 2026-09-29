# survey: see what a site has before reading all of it
For a site too big to read whole (a blog, a shop, a directory): every URL the sitemap and pages name, listed by template with counts, two of each read, no pictures fetched.

## Run
```
tt-crawl survey https://theirsite.com
```

## Read
`raw/site/<host>/_index/templates.md`: each kind of page (posts, products, locations, pagination) with how many there are, how many were read, the layouts seen, the average words, an example. `_index/inventory.md` lists every URL.

## Tell the owner, then ask
In one message: what the site has ("1,240 posts under /blog, 380 products, 14 pages"), roughly how big a full read is (pages times average words; pictures), and which collections they want brought over. Nothing big downloads until they say. Then `tt-crawl pages` for a rebuild, `tt-crawl import --template <t>` for one collection, or `tt-crawl add URL` for a page.
