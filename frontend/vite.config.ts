/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Dev fala com o backend local SEM CORS: o mesmo código de client.ts
    // funciona em dev (proxy) e em produção (FastAPI serve tudo).
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/healthz": "http://127.0.0.1:8000",
    },
  },
  // Vitest + Testing Library (arquitetura §Ferramentas): jsdom é o ambiente
  // do Testing Library; globals liga describe/it/expect; setup carrega o
  // jest-dom. Os testes mockam o módulo client — zero rede.
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
