
const ADMIN_TOKEN_KEY = "library_admin_token";

function getAdminToken() {
  return sessionStorage.getItem(ADMIN_TOKEN_KEY);
}

async function adminLogin(password) {
  const response = await fetch(`${SUBMISSIONS_URL}/admin/login`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ password })
  });

  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.error || "Login failed.");
  }

  sessionStorage.setItem(ADMIN_TOKEN_KEY, data.token);
  return data.token;
}

function adminLogout() {
  sessionStorage.removeItem(ADMIN_TOKEN_KEY);
}

async function adminRequest(path, body = {}) {
  const token = getAdminToken();

  if (!token) {
    throw new Error("Not logged in.");
  }

  const response = await fetch(`${SUBMISSIONS_URL}/admin/${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`
    },
    body: JSON.stringify(body)
  });

  const data = await response.json();

  if (!response.ok) {
    if (response.status === 401) {
      adminLogout();
    }

    throw new Error(data.error || "Admin request failed.");
  }

  return data;
}


const loginSection = document.getElementById("admin-login");
const adminPanel = document.getElementById("admin-panel");
const loginForm = document.getElementById("admin-login-form");
const loginError = document.getElementById("admin-login-error");
const adminStatus = document.getElementById("admin-status");
const logoutButton = document.getElementById("admin-logout");

function showAdminPanel(loggedIn) {
  loginSection.hidden = loggedIn;
  adminPanel.hidden = !loggedIn;
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  loginError.textContent = "";

  const password = document.getElementById("admin-password").value;

  try {
    await adminLogin(password);
    loginForm.reset();

    const returnTo = new URLSearchParams(window.location.search).get("return");

    if (returnTo) {
      const target = new URL(returnTo);

      if (target.origin === window.location.origin) {
        window.location.replace(target.href);
        return;
      }
    }

    window.location.replace(new URL("./", window.location.href).href);
  } catch (error) {
    loginError.textContent = error.message;
  }
});

logoutButton.addEventListener("click", () => {
  adminLogout();
  window.location.replace(new URL("./", window.location.href).href);
});

showAdminPanel(Boolean(getAdminToken()));
