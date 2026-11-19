"use strict";
const language = document.querySelector("#language-select");
const error = document.querySelector("#access-error");
I18n.use(I18n.preferred);
language.addEventListener("change", () => { I18n.use(language.value); error.hidden = true; });
document.querySelector("#access-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.target.querySelector("button");
  button.disabled = true;
  error.hidden = true;
  try {
    const response = await fetch("/api/access", {
      method: "POST", headers: {"Content-Type":"application/json", "X-Requested-With":"SortingLab"},
      body: JSON.stringify({code: document.querySelector("#access-code").value})
    });
    if (response.ok) { window.location.replace("/"); return; }
    const result = await response.json();
    error.textContent = typeof result.detail === "string" ? I18n.server(result.detail) : I18n.t("invalidForm");
  } catch { error.textContent = I18n.t("connectionLost"); }
  error.hidden = false;
  button.disabled = false;
});
