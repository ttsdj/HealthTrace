import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { resolve } from 'path';

const backendTarget = process.env.VITE_BACKEND_TARGET || 'http://localhost:8000';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, './src'),
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash].[ext]',
      },
    },
  },
  server: {
    port: 3000,
    proxy: {
      // 代理后端接口，方便开发联调
      '/auth': backendTarget,
      '/chat': backendTarget,
      '/sessions': backendTarget,
      '/documents': backendTarget,
      '/health': backendTarget,
      '/collections': backendTarget,
      '/care-navigation': backendTarget,
    },
  },
});
