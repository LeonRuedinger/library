// Cloudflare Turnstile (bot protection). Disabled on localhost:8000 for testing.
let turnstileToken = "";

const isLocalTest =
  window.location.hostname === "localhost" &&
  window.location.port === "8000";

window.onTurnstileLoaded = function () {
  if (isLocalTest || typeof turnstile === "undefined") return;

  turnstile.render("#turnstile-widget", {
    sitekey: TURNSTILE_SITEKEY,
    callback: token => { turnstileToken = token; },
    "expired-callback": () => { turnstileToken = ""; },
    "error-callback": () => { turnstileToken = ""; }
  });
};

function resetTurnstile() {
  if (!isLocalTest && typeof turnstile !== "undefined") {
    turnstile.reset();
  }
  turnstileToken = "";
}

if (!isLocalTest) {
  const script = document.createElement("script");
  script.src =
    "https://challenges.cloudflare.com/turnstile/v0/api.js" +
    "?onload=onTurnstileLoaded&render=explicit";
  script.async = true;
  script.defer = true;
  document.head.appendChild(script);
}
