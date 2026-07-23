import { defineConfig } from "vitest/config";
import path from "node:path";

// Resuelve el alias "@/..." (mismo que tsconfig) sin plugins externos.
export default defineConfig({
  resolve: {
    alias: { "@": path.resolve(process.cwd()) },
  },
  test: {
    include: ["lib/**/*.test.ts"],
  },
});
