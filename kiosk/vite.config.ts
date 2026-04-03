import { defineConfig } from 'vite';

export default defineConfig({
  root: 'src',
  publicDir: '../public',
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
  server: {
    proxy: {
      '/grafana': 'http://localhost:3000',
      '/api/chat': 'http://localhost:3002',
      '/api/departures': 'http://localhost:3010',
      '/api/vehicles': 'http://localhost:3010',
      '/weather': 'http://localhost:3020',
      '/news': 'http://localhost:3021',
      '/calendar': 'http://localhost:3022',
      '/nysse': 'http://localhost:3010',
      '/assets': 'http://localhost:3010',
    },
  },
});
