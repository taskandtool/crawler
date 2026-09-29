# brand: learn a business from its own website
Facts, voice and look, for a brain or a website without one: every page the site's own nav names, a sample of each collection, the brand's pictures, the styles and screenshots.

## Run
```
tt-crawl brand https://theirsite.com
tt-crawl docs                                  # price lists, brochures, menus the pages link to
tt-crawl places "Business name, City" --out raw/places   # when a Google Places connection is granted
```
`brand` is `site` with `--images brand --styles --screenshots --per-template 2 --per-section 6`: every page the header links to is read; a blog or a shop is sampled, two of each kind.

## Read, in this order
1. `raw/site/<host>/_index/facts.json`: phones, emails, addresses, hours, social, the book and quote links, each with where it was found. Two values for one fact are both kept: ask the owner which is right.
2. `structured/business.json`: the site's own markup, exact.
3. `pages/`: each page's text word for word, for services, prices, team, FAQs, policies, and the voice.
4. `_index/reviews.md`: quotes verbatim with name, date, platform.
5. `_index/styles.json`, `_index/media.json` and `shots/`: colours, fonts, the logo, the photographs and what each sat beside.

## Tell the owner
Pages read and found, whether the limit was hit (`limit_reached`), pictures, reviews, any fact with two values, and what the site does not say. A missing fact is a question, never a guess.
