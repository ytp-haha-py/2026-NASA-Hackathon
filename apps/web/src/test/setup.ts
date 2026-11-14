import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  // Locale preference and <html lang> must not leak between tests.
  window.localStorage.clear();
  document.documentElement.lang = "en";
});
