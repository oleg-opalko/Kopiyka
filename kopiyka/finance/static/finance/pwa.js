if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/service-worker.js", { scope: "/" }).catch(() => {});
  });
}

let installPrompt = null;
const installButton = document.getElementById("install-app");
const iosInstallHelp = document.getElementById("ios-install-help");
const iosInstallClose = document.getElementById("ios-install-close");
const isAppleMobile = /iPad|iPhone|iPod/.test(navigator.userAgent)
  || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const isInstalled = window.matchMedia("(display-mode: standalone)").matches
  || navigator.standalone === true;

if (isAppleMobile && !isInstalled && installButton) installButton.hidden = false;

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  installPrompt = event;
  if (installButton) installButton.hidden = false;
});

installButton?.addEventListener("click", async () => {
  if (!installPrompt) {
    if (isAppleMobile && iosInstallHelp?.showModal) iosInstallHelp.showModal();
    return;
  }
  installButton.disabled = true;
  await installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt = null;
  installButton.hidden = true;
  installButton.disabled = false;
});

iosInstallClose?.addEventListener("click", () => iosInstallHelp?.close());

window.addEventListener("appinstalled", () => {
  if (installButton) installButton.hidden = true;
});