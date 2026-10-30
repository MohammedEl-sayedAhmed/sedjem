# How the images are made

The README screenshots come from a throwaway copy of the app, filled with the made-up sprint planning meeting in content.py. It runs on a temporary folder, so none of your own transcripts or settings are used. From the repository root, with the project's Python:

```sh
python -P docs/images/make/screenshots.py     # or name some shots: transcript compare ...
python docs/images/make/shrink.py             # needs Pillow
python docs/images/make/social.py             # the social preview card
```

screenshots.py saves the pages at twice their size in out/ (not committed), and shrink.py makes the 2400-pixel files in docs/images/ from them. social.py builds the social preview from the logo in docs/brand/ and the transcript screenshot, so run it after the screenshots.

The website's pictures of the app come from the same script: its shot named site takes one region of the app per claim, light and dark, from a desktop window at twice the size and a phone's at three times, and saves them in out/site/. `python -P docs/images/make/screenshots.py site`, then `python docs/images/make/shrink.py site` makes each a lossless WebP and an optimised PNG in site/captures/, with every pixel and colour kept, as the site shows them at their own size.

The other generated pictures have their tools next to them: the charts are made by docs/charts.py, the logo files by docs/brand/build.py and render.py, and the video, its music and the README GIF as described in docs/video/README.md.
