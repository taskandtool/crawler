"""HTML fixtures shared by the tests. No network anywhere in tests/."""

BUSINESS_PAGE = """<!doctype html><html lang="en-GB"><head>
<title> Acme  Roofing | Leeds </title>
<meta name="description" content="Roofs in Leeds since 1998">
<meta name="robots" content="index,follow">
<meta property="og:title" content="Acme Roofing"><meta property="og:image" content="/img/og.jpg">
<meta name="google-site-verification" content="abc123verify">
<link rel="canonical" href="https://acme.com/">
<link rel="alternate" hreflang="fr" href="https://acme.com/fr/">
<script async src="https://www.googletagmanager.com/gtag/js?id=G-ABC123XYZ"></script>
<script>window.dataLayer=[];gtag('config','G-ABC123XYZ');fbq('init','123456789012');</script>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"RoofingContractor","name":"Acme Roofing",
 "telephone":"+44 113 555 0100","address":{"@type":"PostalAddress","streetAddress":"12 Dock Rd","addressLocality":"Leeds"},
 "sameAs":["https://instagram.com/acme"],"openingHours":["Mo-Fr 08:00-17:00"]}</script>
</head><body>
<header><a href="/"><img src="/img/logo.svg" alt="Acme Roofing"></a>
<nav><ul><li><a href="/services">Services</a><ul><li><a href="/services/flat-roofs">Flat roofs</a></li></ul></li>
<li><a href="/about">About</a></li><li><a href="#content">Skip to content</a></li></ul></nav>
<a class="btn btn-primary" href="tel:+441135550100">Call us</a></header>
<main><h1>Roofs that <b>last</b></h1><p>We fix roofs across Leeds. See our <a href="/brochure.pdf">brochure</a> and <a href="/about">team</a>.</p>
<form action="/contact" method="post"><input name="name" required><input type="hidden" name="_csrf"><input type="email" name="email"><textarea name="message"></textarea><button type="submit">Send</button></form>
<iframe src="https://www.google.com/maps/embed?pb=1"></iframe>
<img src="/img/crew-800x600.jpg" srcset="/img/crew-800x600.jpg 800w, /img/crew-1600x1200.jpg 1600w" alt="The crew on a roof" width="800" height="600">
<img src="/img/icons/check.svg" alt="">
<div itemscope itemtype="https://schema.org/Person"><span itemprop="name">Jane Doe</span><meta itemprop="jobTitle" content="Owner"></div>
</main>
<footer><h4>Company</h4><a href="/about">About</a><a href="/careers">Careers</a>
<h4>Legal</h4><a href="/privacy">Privacy policy</a><a href="/terms">Terms</a>
<a href="https://instagram.com/acme">Instagram</a><a href="https://www.facebook.com/acme">Facebook</a>
<img src="/img/bbb-badge.png" alt="BBB accredited business">
<p>&copy; 2026 Acme Roofing Ltd. All rights reserved.</p></footer>
</body></html>"""

NO_LANDMARKS_PAGE = """<html><head><title>Old site</title></head><body>
<div><a href="/">Home</a> <a href="/about">About</a></div>
<h1>Welcome</h1><p>Some body text.</p>
<div>Copyright 2010 Old Co</div></body></html>"""

WP_HTML = """<html><head><link rel="https://api.w.org/" href="https://blog.example.com/news/wp-json/" /></head><body></body></html>"""
