/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: "./src/setupTests.ts",
    globals: true,
    // Vitest stubs `.css` imports with an empty module by default — including a `?raw` one, which
    // would leave src/styles/tokens.test.ts asserting about empty strings and passing forever.
    // With processing on, a stylesheet reaches the test as its real text (and as real rules in
    // jsdom, so a `toBeVisible()` assertion is about the stylesheet too, not only the markup).
    css: true,
  },
});
