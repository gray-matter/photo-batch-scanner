import { initModals } from "./js/modals.js";
import { initGallery } from "./js/gallery.js";
import { initScanStatus } from "./js/scan-status.js";
import { initTagging } from "./js/tagging.js";
import { initReview } from "./js/review.js";

const modals = initModals();
let review;
const scanStatus = initScanStatus({
  getReviewRaw: () => review.getRaw(),
  openReview: (...args) => review.open(...args),
});
let tagging;
const gallery = initGallery({
  ...modals,
  onTagSelected: () => tagging.open(),
  setStatus: scanStatus.setStatus,
});
tagging = initTagging({ ...gallery, setStatus: scanStatus.setStatus });
review = initReview({
  askConfirm: modals.askConfirm,
  setScanBlocked: scanStatus.setBlocked,
  setStatus: scanStatus.setStatus,
  loadPhotos: gallery.loadPhotos,
});

gallery.loadPhotos();
scanStatus.start();
