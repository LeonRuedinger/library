// Live filter for the book grid (used on the index and archive pages).
(() => {
  const input = document.getElementById("q");
  const counter = document.getElementById("n");
  const items = [...document.querySelectorAll("[data-s]")];

  input.addEventListener("input", () => {
    const query = input.value.trim().toLowerCase();
    let visible = 0;

    items.forEach(item => {
      const match = !query || item.dataset.s.includes(query);
      item.hidden = !match;
      if (match) visible++;
    });

    counter.textContent = counter.dataset.t.replace("{n}", visible);
  });
})();
