// Site settings.
//   aiEndpoint: the Vercel function that writes emails with AI (see ai-proxy/README.md); empty = template writer only.
//   shareBase:  the public address used in shared links and link previews. Change it here only:
//               scripts/share_pages.py reads it too.
window.COMPASS_CONFIG = {
  aiEndpoint: "https://research-compass-ai-proxy.vercel.app/api/draft",
  shareBase: "https://research-compass.studentlabs.me/",
};
