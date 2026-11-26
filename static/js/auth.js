(() => {
  const ADMIN_TOKEN_KEY = "library_admin_token";

  // Determine the site root, including when this script runs
  // from a nested book page.
  const siteRoot = new URL("../../", document.currentScript.src);

  // The login page must always remain accessible.
  if (window.location.pathname.endsWith("/admin.html")) {
    return;
  }

  // Redirect visitors without an admin session to the login page.
if (!sessionStorage.getItem(ADMIN_TOKEN_KEY)) {
const returnTo = window.location.href;
const loginUrl = new URL("admin.html", siteRoot);
loginUrl.searchParams.set("return", returnTo);

window.location.replace(loginUrl.href);
} else {
document.addEventListener("DOMContentLoaded", () => {
document.querySelectorAll(".admin-book-actions").forEach((actions) => {
  actions.hidden = false;
});
});
}
})();