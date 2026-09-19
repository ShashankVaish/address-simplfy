# Landing page

A single self-contained HTML file: no build step, no dependencies beyond Google
Fonts. Open `index.html` in a browser, or host the folder on any static host.

Everything on the page is taken from the docs and the measured results in
`docs/results/`; when those numbers change, change them here too. The Cedar
demo mirrors `policies/contact.cedar` for illustration — the deployed
authorizer runs the real engine.

Amplify Hosting: add an app pointed at this repo with the build root set to
`frontend/landing` and no build command.
