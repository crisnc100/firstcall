const health = document.querySelector<HTMLParagraphElement>("#health");

async function showHealth(): Promise<void> {
  if (!health) return;
  try {
    const response = await fetch("/healthz");
    health.textContent = response.ok ? "The API is up." : `The API answered ${response.status}.`;
  } catch {
    health.textContent = "The API is not reachable.";
  }
}

void showHealth();
