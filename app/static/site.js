(() => {
  const header = document.querySelector(".site-header");
  const toggle = document.querySelector(".nav-toggle");
  const navigation = document.querySelector(".site-nav");

  if (!header || !toggle || !navigation) return;

  const mobileQuery = window.matchMedia("(max-width: 760px)");

  const closeMenu = () => {
    header.classList.remove("nav-open");
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", "Open navigation");
  };

  toggle.addEventListener("click", () => {
    const isOpen = header.classList.toggle("nav-open");
    toggle.setAttribute("aria-expanded", String(isOpen));
    toggle.setAttribute("aria-label", isOpen ? "Close navigation" : "Open navigation");
  });

  navigation.addEventListener("click", (event) => {
    if (event.target.closest("a")) closeMenu();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenu();
  });

  document.addEventListener("click", (event) => {
    if (!header.contains(event.target)) closeMenu();
  });

  const updateNavigationMode = () => {
    closeMenu();
    toggle.hidden = !mobileQuery.matches;
  };

  mobileQuery.addEventListener("change", updateNavigationMode);
  updateNavigationMode();
})();
