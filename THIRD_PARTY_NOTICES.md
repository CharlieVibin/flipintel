# Third-party notices

## cex-uk-prices

`app/pricing_cex.py` adapts the product-matching approach and parts of the
response handling from the **cex-uk-prices** project:

- Source: https://github.com/casareanderson/cex-uk-prices
- Copyright (c) 2026 Christian Asare-Anderson
- Licence: MIT

The reference copy added to the workspace was temporary. It was inspected,
the relevant logic was reimplemented in this repository, and the reference
folder was then removed. No module imports from it at runtime.

The match-score veto, the noise-word list, the model-token rule and the measured
0.6 match threshold are adapted from that project. This repository additionally
requires an exact variant/capacity match, selects the undecorated CeX product
cohort, and rejects cohorts whose prices disagree instead of blending them.

### MIT licence text

```
MIT License

Copyright (c) 2026 Christian Asare-Anderson

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
