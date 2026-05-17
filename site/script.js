(function () {
  const year = document.querySelector("#year");
  if (year) {
    year.textContent = String(new Date().getFullYear());
  }

  const repoLink = document.querySelector("#repo-link");
  if (repoLink) {
    const host = window.location.hostname;
    const path = window.location.pathname.split("/").filter(Boolean)[0];
    if (host.endsWith("github.io") && path) {
      const owner = host.replace(".github.io", "");
      repoLink.href = `https://github.com/${owner}/${path}`;
    } else {
      repoLink.href = "https://github.com/";
    }
  }

  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.querySelector(button.dataset.copy);
      if (!target) return;

      const text = target.textContent.trim();
      try {
        await navigator.clipboard.writeText(text);
        button.querySelector("span").textContent = "Copied";
        window.setTimeout(() => {
          button.querySelector("span").textContent = "Copy";
        }, 1400);
      } catch {
        const range = document.createRange();
        range.selectNodeContents(target);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      }
    });
  });

  if (window.lucide) {
    window.lucide.createIcons({
      attrs: {
        "stroke-width": 2,
      },
    });
  }
})();
