(() => {
  const gallery = document.querySelector(".markdown-body");
  const lightbox = document.querySelector(".image-lightbox");

  if (!gallery || !lightbox) return;

  const images = [...gallery.querySelectorAll("img")];
  if (!images.length) return;

  const lightboxImage = lightbox.querySelector("figure img");
  const caption = lightbox.querySelector(".image-lightbox-caption");
  const count = lightbox.querySelector(".image-lightbox-count");
  const previousButton = lightbox.querySelector(".image-lightbox-previous");
  const nextButton = lightbox.querySelector(".image-lightbox-next");
  const closeButton = lightbox.querySelector(".image-lightbox-close");
  let currentIndex = 0;

  const showImage = (index) => {
    currentIndex = (index + images.length) % images.length;
    const image = images[currentIndex];
    lightboxImage.src = image.currentSrc || image.src;
    lightboxImage.alt = image.alt;
    caption.textContent = image.alt || "Screenshot";
    count.textContent = `${currentIndex + 1} / ${images.length}`;
  };

  const openLightbox = (index) => {
    showImage(index);
    lightbox.showModal();
  };

  images.forEach((image, index) => {
    image.tabIndex = 0;
    image.setAttribute("role", "button");
    image.setAttribute("aria-label", `Open ${image.alt || "image"} in screenshot viewer`);
    image.addEventListener("click", () => openLightbox(index));
    image.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openLightbox(index);
      }
    });
  });

  previousButton.addEventListener("click", () => showImage(currentIndex - 1));
  nextButton.addEventListener("click", () => showImage(currentIndex + 1));
  closeButton.addEventListener("click", () => lightbox.close());

  lightbox.addEventListener("click", (event) => {
    if (event.target === lightbox) lightbox.close();
  });

  lightbox.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") showImage(currentIndex - 1);
    if (event.key === "ArrowRight") showImage(currentIndex + 1);
  });
})();
