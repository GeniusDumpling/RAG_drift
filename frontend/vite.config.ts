import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  build: {
    rollupOptions: {
      output: {
        onlyExplicitManualChunks: true,
        // Keep G6's mutually dependent base classes in one chunk when loading its 3D extension.
        manualChunks(id) {
          if (id.includes('/node_modules/@antv/g6/')) return 'g6-core';
        },
      },
    },
  },
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
  },
});
