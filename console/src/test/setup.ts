import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

// jsdom has no canvas or matchMedia, which xterm needs to render; pages only need the terminal to mount.
vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    loadAddon() {}
    open() {}
    onData() {}
    write() {}
    reset() {}
    focus() {}
    dispose() {}
  },
}));
vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    fit() {}
  },
}));
vi.mock("@xterm/xterm/css/xterm.css", () => ({}));

// Not in jsdom; the terminal panel observes its size to refit.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
