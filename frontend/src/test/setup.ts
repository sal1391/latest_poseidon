import "@testing-library/jest-dom/vitest";

// jsdom implements no layout, so scrollIntoView is absent; the thread calls it
// to keep the newest message in view.
Element.prototype.scrollIntoView = () => {};
